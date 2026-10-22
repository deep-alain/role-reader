"""Image-only preferences based on source-bound local CLIP vectors.

Cosine similarity is ranking evidence, never an age or image-safety decision.
This store is independent of story feedback and never changes its filters.
"""
import copy
import json
import math
import numbers
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from pool_avatars import avatar_file
from selection_rules import adult_state

DIMENSIONS = 512
DEFAULT_LABEL = '相近封面（不露点）'


def normalized_embedding(value):
    """Validate and normalize without NumPy, including very small/large inputs."""
    if isinstance(value, (str, bytes, dict)):
        raise ValueError('封面向量必须包含 512 个有限数值')
    try:
        vector = list(value)
    except (TypeError, ValueError):
        raise ValueError('封面向量必须包含 512 个有限数值') from None
    if len(vector) != DIMENSIONS or any(isinstance(v, bool) or not isinstance(v, numbers.Real) for v in vector):
        raise ValueError('封面向量必须包含 512 个有限数值')
    try:
        vector = [float(v) for v in vector]
    except (ValueError, OverflowError):
        raise ValueError('封面向量包含无效数值') from None
    if not all(math.isfinite(v) for v in vector):
        raise ValueError('封面向量包含无效数值')
    scale = max(abs(v) for v in vector)
    if scale == 0:
        raise ValueError('封面向量不能为零向量')
    scaled = [v / scale for v in vector]
    norm = math.sqrt(math.fsum(v * v for v in scaled))
    return [v / norm for v in scaled]


def _model_version(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError('缺少有效的封面图像模型版本')
    return value


class CoverPreferences:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'cover_preferences.json'
        self.embeddings_path = self.directory / 'cover_embeddings.json'
        self.lock = threading.RLock()
        self.references = {}
        self.embeddings = {}
        for path, key, target in ((self.path, 'references', self.references), (self.embeddings_path, 'embeddings', self.embeddings)):
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                values = saved.get(key, {}) if isinstance(saved, dict) else {}
                if isinstance(values, dict):
                    target.update(values)
            except (OSError, ValueError):
                pass

    def _save(self, path, key, values):
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'version': 1, key: values}, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        os.replace(temp, path)

    def _asset(self, record, require_adult=False):
        if not isinstance(record, dict):
            raise ValueError('缺少来源角色资料')
        asset = record.get('avatarAsset') or {}
        source = record.get('avatarSource')
        if not isinstance(asset, dict) or not isinstance(source, str) or not source or asset.get('source') != source:
            raise ValueError('封面必须绑定当前来源头像')
        digest = asset.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('封面缺少有效的 SHA256')
        if asset.get('review') != 'approved':
            raise ValueError('封面必须先通过来源头像检查')
        if require_adult and adult_state(record) != 'verified':
            raise ValueError('封面偏好仅用于已明确成年的角色')
        # This verifies the bytes as well as the metadata, rejecting stale caches.
        try:
            avatar_file(self.directory, record)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError('来源头像文件缺失或 SHA256 已改变') from error
        return digest

    @staticmethod
    def _public(reference):
        return {key: copy.deepcopy(reference[key]) for key in ('id', 'source', 'name', 'sha256', 'modelVersion', 'label', 'at') if key in reference}

    def set_reference(self, record, embedding, model_version, label=DEFAULT_LABEL):
        digest = self._asset(record, require_adult=True)
        vector = normalized_embedding(embedding)
        model_version = _model_version(model_version)
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ValueError('封面偏好标签必须为 1 至 200 个字符')
        reference = {'id': record.get('id', ''), 'source': record.get('source', ''), 'name': record.get('name', ''),
                     'sha256': digest, 'modelVersion': model_version, 'label': label.strip(),
                     'at': datetime.now(timezone.utc).isoformat(), 'embedding': vector,
                     'avatarSource': record['avatarSource'], 'avatarAsset': copy.deepcopy(record['avatarAsset'])}
        with self.lock:
            self.references[digest] = reference
            self.embeddings[self._key(digest, model_version)] = {'sha256': digest, 'modelVersion': model_version, 'embedding': vector}
            self._save(self.path, 'references', self.references)
            self._save(self.embeddings_path, 'embeddings', self.embeddings)
        return self._public(reference)

    def clear_reference(self, digest):
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('缺少有效的封面 SHA256')
        with self.lock:
            removed = self.references.pop(digest, None) is not None
            if removed:
                self._save(self.path, 'references', self.references)
            return removed

    def all_references(self):
        """Safe public metadata; vectors and private file metadata stay internal."""
        with self.lock:
            return [self._public(value) for value in self.references.values() if isinstance(value, dict)]

    def snapshot(self):
        references = self.all_references()
        return {'count': len(references), 'references': references,
                'explanation': '仅按已通过检查的成年来源封面与参考图的实际图像相似度加权，不改变题材偏好、来源分级或入库门槛。'}

    @staticmethod
    def _key(digest, model_version):
        return model_version + ':' + digest

    def put_embedding(self, record, vector, modelVersion):
        digest = self._asset(record)
        embedding = normalized_embedding(vector)
        model_version = _model_version(modelVersion)
        with self.lock:
            self.embeddings[self._key(digest, model_version)] = {'sha256': digest, 'modelVersion': model_version, 'embedding': embedding}
            self._save(self.embeddings_path, 'embeddings', self.embeddings)
        return True

    def get_embedding(self, record, modelVersion):
        try:
            digest = self._asset(record)
            model_version = _model_version(modelVersion)
            with self.lock:
                value = self.embeddings.get(self._key(digest, model_version), {})
                if value.get('sha256') != digest or value.get('modelVersion') != model_version:
                    return None
                return normalized_embedding(value.get('embedding'))
        except (ValueError, TypeError, AttributeError):
            return None

    def evaluate(self, record, embedding, model_version):
        try:
            digest = self._asset(record, require_adult=True)
            vector = normalized_embedding(embedding)
            model_version = _model_version(model_version)
        except (ValueError, TypeError):
            return None
        best = None
        with self.lock:
            for reference in self.references.values():
                if not isinstance(reference, dict) or reference.get('modelVersion') != model_version:
                    continue
                try:
                    # A reference file being replaced must not silently train a new look.
                    reference_digest = self._asset(reference)
                    if reference_digest != reference.get('sha256'):
                        continue
                    reference_vector = normalized_embedding(reference.get('embedding'))
                except (ValueError, TypeError):
                    continue
                score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(vector, reference_vector))))
                if best is None or score > best['score']:
                    best = {'score': score, 'referenceName': reference.get('name', ''), 'label': reference.get('label', DEFAULT_LABEL),
                            'referenceSha256': reference_digest, 'modelVersion': model_version, 'sha256': digest}
        return best
