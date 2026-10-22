import copy
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from cover_preferences import CoverPreferences, normalized_embedding
from pool_avatars import cache_avatar
from test_pool_avatars import PNG


def vector(first=1, second=0):
    return [first, second] + [0] * 510


class CoverPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.store = CoverPreferences(self.directory)
        self.reference = self.record('Lindsey', 'a' * 24)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, name, key, data=PNG):
        record = {'id': key, 'name': name, 'source': 'https://example.org/' + key, 'age': '22',
                  'avatarSource': 'https://example.org/' + key + '.png'}
        record['avatarAsset'] = cache_avatar(self.directory, record, data)
        record['avatarAsset']['review'] = 'approved'
        return record

    def test_normalization_is_stable_and_rejects_invalid_vectors(self):
        for value in (1e-300, 1e300):
            result = normalized_embedding(vector(value, value))
            self.assertAlmostEqual(math.fsum(v * v for v in result), 1)
        for value in (None, 'text', {}, [], [1] * 511, [True] * 512, [0] * 512, vector(float('nan')), vector(float('inf'))):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                normalized_embedding(value)

    def test_reference_persists_separately_and_snapshot_never_exposes_vectors(self):
        original = copy.deepcopy(self.reference)
        result = self.store.set_reference(self.reference, vector(2), 'clip-a')
        self.assertEqual(result['name'], 'Lindsey')
        self.assertNotIn('embedding', json.dumps(self.store.snapshot()))
        self.assertNotIn('avatarAsset', json.dumps(self.store.snapshot()))
        self.assertFalse((self.directory / 'taste_profile.json').exists())
        self.assertEqual(self.reference, original)
        reopened = CoverPreferences(self.directory)
        self.assertEqual(reopened.snapshot()['count'], 1)
        self.assertEqual(reopened.get_embedding(self.reference, 'clip-a'), vector())
        self.assertEqual(reopened.evaluate(self.reference, vector(), 'clip-a')['score'], 1)

    def test_similarity_uses_real_vectors_and_selects_best_reference(self):
        self.store.set_reference(self.reference, vector(), 'clip-a')
        second = self.record('Other', 'b' * 24, PNG + b'other')
        self.store.set_reference(second, vector(0, 1), 'clip-a', '另一张参考')
        near = self.record('Near', 'c' * 24, PNG + b'near')
        far = self.record('Far', 'd' * 24, PNG + b'far')
        self.assertGreater(self.store.evaluate(near, vector(3, 1), 'clip-a')['score'],
                           self.store.evaluate(far, vector(1, 1), 'clip-a')['score'])
        result = self.store.evaluate(near, vector(1, 3), 'clip-a')
        self.assertEqual(result['referenceName'], 'Other')
        self.assertEqual(result['referenceSha256'], second['avatarAsset']['sha256'])
        self.assertEqual(result['sha256'], near['avatarAsset']['sha256'])

    def test_same_image_across_sources_is_one_reference_and_can_be_cleared(self):
        self.store.set_reference(self.reference, vector(), 'clip-a')
        mirror = self.record('Mirror', 'b' * 24)
        self.store.set_reference(mirror, vector(0, 1), 'clip-a')
        self.assertEqual(self.store.snapshot()['count'], 1)
        self.assertEqual(self.store.all_references()[0]['name'], 'Mirror')
        digest = mirror['avatarAsset']['sha256']
        self.assertTrue(self.store.clear_reference(digest))
        self.assertFalse(self.store.clear_reference(digest))
        self.assertIsNone(CoverPreferences(self.directory).evaluate(mirror, vector(), 'clip-a'))

    def test_unreviewed_mismatched_and_invalid_digest_are_not_eligible(self):
        self.store.set_reference(self.reference, vector(), 'clip-a')
        for changes in ({'review': 'pending'}, {'source': 'https://example.org/wrong'}, {'sha256': 'invalid'}):
            record = copy.deepcopy(self.reference)
            record['avatarAsset'].update(changes)
            self.assertIsNone(self.store.evaluate(record, vector(), 'clip-a'))
            self.assertIsNone(self.store.get_embedding(record, 'clip-a'))
            with self.assertRaises(ValueError):
                self.store.set_reference(record, vector(), 'clip-a')
            with self.assertRaises(ValueError):
                self.store.put_embedding(record, vector(), 'clip-a')

    def test_unknown_minor_and_school_setting_never_receive_similarity_scores(self):
        self.store.set_reference(self.reference, vector(), 'clip-a')
        for changes in ({'age': ''}, {'age': '17'}, {'age': '22', 'prompt': 'A high school student.'}):
            record = {**self.reference, **changes}
            self.assertIsNone(self.store.evaluate(record, vector(), 'clip-a'))
            with self.assertRaises(ValueError):
                self.store.set_reference(record, vector(), 'clip-a')

    def test_model_version_is_exact_and_invalid_models_are_rejected(self):
        self.store.set_reference(self.reference, vector(), 'clip-a')
        self.assertIsNone(self.store.evaluate(self.reference, vector(), 'clip-b'))
        self.assertIsNone(self.store.get_embedding(self.reference, 'clip-b'))
        for value in (None, '', 'a\n', 'x' * 257):
            with self.assertRaises(ValueError):
                self.store.put_embedding(self.reference, vector(), value)

    def test_candidate_bytes_changed_invalidates_embedding_and_similarity(self):
        self.store.set_reference(self.reference, vector(), 'clip-a')
        candidate = self.record('Candidate', 'b' * 24, PNG + b'candidate')
        self.store.put_embedding(candidate, vector(3, 4), 'clip-a')
        path = self.directory / 'avatars' / candidate['avatarAsset']['file']
        path.write_bytes(PNG + b'changed')
        self.assertIsNone(self.store.get_embedding(candidate, 'clip-a'))
        self.assertIsNone(self.store.evaluate(candidate, vector(3, 4), 'clip-a'))

    def test_reference_bytes_changed_cannot_train_another_look(self):
        self.store.set_reference(self.reference, vector(), 'clip-a')
        candidate = self.record('Candidate', 'b' * 24, PNG + b'candidate')
        path = self.directory / 'avatars' / self.reference['avatarAsset']['file']
        path.write_bytes(PNG + b'changed')
        self.assertIsNone(self.store.evaluate(candidate, vector(), 'clip-a'))

    def test_changed_declared_sha_cannot_reuse_cached_vector(self):
        self.store.put_embedding(self.reference, vector(), 'clip-a')
        replacement = copy.deepcopy(self.reference)
        replacement['avatarAsset']['sha256'] = hashlib.sha256(PNG + b'replacement').hexdigest()
        self.assertIsNone(self.store.get_embedding(replacement, 'clip-a'))

    def test_bad_persisted_vectors_fail_closed_and_do_not_appear_in_scores(self):
        self.store.set_reference(self.reference, vector(), 'clip-a')
        saved = json.loads(self.store.path.read_text(encoding='utf-8'))
        saved['references'][self.reference['avatarAsset']['sha256']]['embedding'] = [1, 2]
        self.store.path.write_text(json.dumps(saved), encoding='utf-8')
        reopened = CoverPreferences(self.directory)
        self.assertIsNone(reopened.evaluate(self.reference, vector(), 'clip-a'))
        reopened.embeddings[reopened._key(self.reference['avatarAsset']['sha256'], 'clip-a')]['embedding'] = vector(float('nan'))
        self.assertIsNone(reopened.get_embedding(self.reference, 'clip-a'))

    def test_embedding_cache_is_bound_to_sha_but_reused_for_a_source_bound_mirror(self):
        self.store.put_embedding(self.reference, vector(3, 4), 'clip-a')
        mirror = self.record('Mirror', 'b' * 24)
        self.assertAlmostEqual(self.store.get_embedding(mirror, 'clip-a')[0], .6)
        self.assertAlmostEqual(self.store.get_embedding(mirror, 'clip-a')[1], .8)
        returned = self.store.get_embedding(mirror, 'clip-a')
        returned[0] = 0
        self.assertAlmostEqual(self.store.get_embedding(mirror, 'clip-a')[0], .6)


if __name__ == '__main__':
    unittest.main()
