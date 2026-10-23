"""Bounded, durable catalogue-to-shelf jobs using the shelf's existing writer.

The controller never consumes stock or approves an image by guessing. A reviewer
may return ``{style, medium?}``, or ``{decision: 'defer', reason}``. Its optional
``available()`` method can return a bool or a mapping with available/reason.
"""
import copy
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from pool_policy import evaluate
from pool_sources import now_iso


class AutomationJobs:
    def __init__(self, directory, pool, registry=None, portrait_reviewer=None,
                 discovery=None, discovery_sources=('crushon', 'spicychat')):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'automation_jobs.json'
        self.pool = pool
        self.registry = registry or pool.client
        self.reviewer = portrait_reviewer
        self.discovery = discovery
        self.discovery_sources = tuple(discovery_sources) if discovery else ()
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = None
        self.closed = False
        self.state = {'version': 1, 'status': 'idle', 'active': False}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict) or not isinstance(saved.get('status'), str):
                raise ValueError('自动读取进度文件无效，已保留原文件')
            self.state = saved
            if self.state.get('active'):
                self.state.update(active=False, status='interrupted', finishedAt=now_iso(),
                                  message='上次读取因服务退出而中断；可重新开始，已入库资料仍保留。')
                self._save()

    def _save(self):
        with self.lock:
            self.state['updatedAt'] = now_iso()
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            os.replace(tmp, self.path)

    def _update(self, **changes):
        with self.lock:
            self.state.update(changes)
            self._save()

    def snapshot(self):
        with self.lock:
            out = copy.deepcopy(self.state)
        # Queue URLs and the internal identity sets are durable but need not be
        # sent repeatedly to the browser during progress polling.
        for key in ('baselineIds', 'baselineReadyIds', 'baselineQualifiedIds', 'qualifiedIds',
                    'qualifiedSources', 'readyAddedIds', 'reviewAttempts'):
            out.pop(key, None)
        for cursor in out.get('cursors', {}).values():
            cursor['queuedCount'] = len(cursor.pop('queue', []))
        out['stage'] = out.get('currentStage', '')
        out['rejected'] = out.get('readRejected', 0) + out.get('reviewRejected', 0)
        for stat in out.get('perSource', {}).values():
            stat.setdefault('status', 'error' if stat.get('errors') else out.get('status', 'idle'))
            stat.setdefault('pending', 0)
            stat.setdefault('rejected', 0)
        return {'job': out}

    def start(self, max_read=120, sources=None):
        if type(max_read) is not int or not 1 <= max_read <= 500:
            raise ValueError('每批读取数量必须是 1 至 500 的整数')
        configured = tuple(self.registry.sources)
        selected = list(configured) if sources is None else sources
        if (not isinstance(selected, (tuple, list)) or not selected or
                any(not isinstance(s, str) for s in selected) or
                len(set(selected)) != len(selected) or any(s not in configured for s in selected)):
            raise ValueError('请选择已配置且支持完整读取的来源')
        with self.lock:
            if self.closed:
                raise ValueError('自动读取服务已关闭')
            if self.state.get('active'):
                return {**self.snapshot(), 'alreadyRunning': True}
            records = self._records()
            availability, reason = self._reviewer_availability()
            previous = self.state
            cursors = {}
            for source in selected:
                cursor = (previous.get('cursors') or {}).get(source) or {}
                page = cursor.get('page', 1)
                pages = cursor.get('queryPages') or {}
                cursors[source] = {
                    'page': page if type(page) is int and page > 0 else 1,
                    'queryPages': {key: value for key, value in pages.items()
                                   if isinstance(key, str) and type(value) is int and value > 0},
                    'queryExhausted': {key: value for key, value in (cursor.get('queryExhausted') or {}).items()
                                       if isinstance(key, str) and type(value) in (int, float) and value > time.time()},
                    'queue': copy.deepcopy([item for item in cursor.get('queue', [])
                                             if isinstance(item, dict) and isinstance(item.get('url'), str)][:100]),
                    'exhausted': False,
                }
            self.cancel_event.clear()
            self.state = {
                'version': 1, 'id': uuid.uuid4().hex, 'status': 'running', 'active': True,
                'startedAt': now_iso(), 'finishedAt': '', 'maxRead': max_read,
                'sources': list(selected), 'read': 0, 'readSucceeded': 0, 'accepted': 0,
                'readyAdded': 0, 'futureReady': 0, 'pending': 0, 'pendingAdded': 0, 'deferred': 0,
                'reviewed': 0, 'reviewRejected': 0, 'reviewDeferred': 0,
                'readRejected': 0,
                'skippedKnown': 0, 'catalogues': 0, 'catalogueLimit': max(8, min(100, max_read * 2)),
                'metadataFound': 0,
                'perSource': {s: {'catalogues': 0, 'discovered': 0, 'read': 0,
                                    'readSucceeded': 0, 'accepted': 0, 'errors': 0, 'error': '', 'mode': 'full'} for s in selected},
                'cursors': cursors,
                'sourceTurn': previous.get('sourceTurn', 0), 'queryTurn': previous.get('queryTurn', 0), 'reasonCounts': {},
                'reviewerAvailable': availability, 'reviewReason': reason,
                'baselineIds': [r['id'] for group in records.values() for r in group],
                'baselineReadyIds': [r['id'] for g in ('ready', 'reserve') for r in records[g]],
                'baselineQualifiedIds': self._qualified(records), 'qualifiedIds': [],
                'qualifiedSources': {}, 'readyAddedIds': [], 'reviewAttempts': [],
                'message': '正在读取各来源目录、完整资料，并检查重复、分级、年龄、热度和头像。',
            }
            self._save()
            paused = getattr(self.pool, 'collection_paused', None)
            if paused is not None:
                paused.set()
            self.thread = threading.Thread(target=self._run, name='role-reader-batch', daemon=True)
            self.thread.start()
            return {**self.snapshot(), 'alreadyRunning': False}

    def _discover_metadata(self):
        summaries = []
        for source in self.discovery_sources:
            if self.cancel_event.is_set():
                break
            with self.lock:
                self.state['perSource'][source] = {'mode': 'metadata', 'catalogues': 1, 'discovered': 0,
                                                   'read': 0, 'readSucceeded': 0, 'accepted': 0,
                                                   'errors': 0, 'error': '', 'status': 'reading'}
                self.state.update(currentStage='discovery', currentSource=source)
                self._save()
            try:
                result = self.discovery(source)
                if not isinstance(result, dict):
                    raise ValueError('来源目录未返回有效结果')
                rows = result.get('items', result.get('candidates', []))
                if not isinstance(rows, list):
                    raise ValueError('来源目录未返回有效列表')
                rows = rows[:100]
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    # Metadata-only channels never provide shelf-ready records.
                    entry = {k: row[k] for k in ('id', 'name', 'url', 'rating', 'metrics', 'sourceRating', 'sourceMetrics') if k in row}
                    entry['source'] = source
                    summaries.append(entry)
                with self.lock:
                    stat = self.state['perSource'][source]
                    stat.update(discovered=sum(s['source'] == source for s in summaries), status=str(result.get('status') or 'authorization_required')[:80],
                                note=str(result.get('note') or result.get('message') or '仅目录；尚未取得可入库的完整设定。')[:300])
                    self.state['metadataFound'] += stat['discovered']
                    self._save()
            except Exception as error:
                self._source_error(source, error)
        if self.discovery_sources:
            path = self.directory / 'automation_candidates.json'
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'jobId': self.state['id'], 'updatedAt': now_iso(), 'items': summaries},
                                      ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            os.replace(tmp, path)

    def cancel(self):
        with self.lock:
            if self.state.get('active'):
                self.cancel_event.set()
                self.state.update(status='cancelling', message='正在停止；已读取的资料会保留。')
                self._save()
            return self.snapshot()

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_event.set()
            thread = self.thread
        # Readers have their own bounded network timeouts. Wait before the shelf
        # releases its writer lock so a late response cannot write a closed pool.
        if thread and thread is not threading.current_thread():
            thread.join()
        paused = getattr(self.pool, 'collection_paused', None)
        if paused is not None:
            paused.clear()

    def _records(self):
        with self.pool.lock:
            return {g: copy.deepcopy(self.pool.state.get(g, []))
                    for g in ('ready', 'reserve', 'portraitPending', 'portraitDeferred')}

    @staticmethod
    def _qualified(records):
        return [r['id'] for group, rows in records.items() for r in rows
                if group in ('ready', 'reserve') or group == 'portraitPending'
                and (r.get('avatarAsset') or {}).get('review') == 'approved']

    def _reconcile(self):
        records = self._records()
        qualified = set(self._qualified(records))
        with self.lock:
            fresh = qualified - set(self.state['baselineQualifiedIds'])
            self.state['qualifiedIds'] = sorted(set(self.state['qualifiedIds']) | fresh)
            self.state['accepted'] = len(self.state['qualifiedIds'])
            baseline = set(self.state['baselineIds'])
            self.state['readyAddedIds'] = sorted(set(self.state['readyAddedIds']) |
                {r['id'] for g in ('ready', 'reserve') for r in records[g] if r['id'] not in self.state['baselineReadyIds']})
            self.state['readyAdded'] = len(self.state['readyAddedIds'])
            self.state['futureReady'] = sum(r['id'] in self.state['qualifiedIds'] for r in records['portraitPending'])
            unresolved = records['portraitPending'] + records['portraitDeferred']
            self.state['pending'] = sum((r.get('avatarAsset') or {}).get('review') != 'approved' for r in unresolved)
            self.state['pendingAdded'] = sum(r['id'] not in baseline for r in unresolved)
            self.state['deferred'] = len(records['portraitDeferred'])
            for rows in records.values():
                for row in rows:
                    if row['id'] in fresh:
                        self.state['qualifiedSources'][row['id']] = row.get('catalogueSource') or row.get('metricsSource')
            for source, stat in self.state['perSource'].items():
                stat['accepted'] = sum(s == source for s in self.state['qualifiedSources'].values())
                stat['pending'] = sum((r.get('catalogueSource') or r.get('metricsSource')) == source
                                      and (r.get('avatarAsset') or {}).get('review') != 'approved'
                                      for r in unresolved)
            self._save()
        return records

    def _reviewer_availability(self):
        if self.reviewer is None:
            return False, '未配置可用的自动头像检查；新头像需要人工确认。'
        try:
            available = getattr(self.reviewer, 'available', None)
            value = available() if callable(available) else True
            if isinstance(value, dict):
                return bool(value.get('available')), str(value.get('reason') or '')[:300]
            if isinstance(value, tuple):
                return bool(value[0]), str(value[1] if len(value) > 1 else '')[:300]
            return bool(value), '' if value else '自动头像检查暂不可用。'
        except Exception as error:
            return False, '自动头像检查暂不可用：' + str(error)[:200]

    def _reason(self, reason):
        text = str(reason or '筛选未通过')[:200]
        if '去重' in text or '重复' in text:
            key = 'duplicate'
        elif '热度' in text:
            key = 'lowHeat'
        elif '年龄' in text or '成年' in text:
            key = 'age'
        elif '头像' in text or '人像' in text:
            key = 'image'
        else:
            key = 'policy'
        with self.lock:
            self.state['reasonCounts'][key] = self.state['reasonCounts'].get(key, 0) + 1
            self.state['lastReason'] = text
            self._save()

    def _review_pending(self):
        available, reason = self._reviewer_availability()
        self._update(reviewerAvailable=available, reviewReason=reason)
        if not available:
            return
        grouped = self._records()
        records = grouped['portraitPending'] + grouped['portraitDeferred']
        # At most a few checks per iteration keep cancellation and acquisition
        # of the shelf's collection lock responsive.
        attempted = set(self.state['reviewAttempts'])
        for record in records[:]:
            if self.cancel_event.is_set():
                break
            asset = record.get('avatarAsset') or {}
            digest = asset.get('sha256')
            key = str(record.get('id')) + ':' + str(digest)
            if asset.get('review') == 'approved' or not digest or key in attempted:
                continue
            attempted.add(key)
            self._update(reviewAttempts=sorted(attempted), currentStage='review', currentName=record.get('name', ''))
            try:
                result = self.reviewer(copy.deepcopy(record))
                if self.cancel_event.is_set():
                    break
                if not isinstance(result, dict):
                    result = {'decision': 'defer', 'reason': '自动检查未返回确定结果。'}
                style = result.get('style')
                if result.get('decision') == 'reject' and not style:
                    style = 'other_rejected'
                if not style or result.get('decision') == 'defer':
                    reason = str(result.get('reason') or '头像仍需人工确认。')[:300]
                    self._defer_record(record, reason)
                    self._update(reviewDeferred=self.state['reviewDeferred'] + 1,
                                 reviewReason=reason)
                    continue
                if result.get('evidence') is not None:
                    outcome = self.pool.review_portrait(record['id'], digest, style, result.get('medium'), evidence=result['evidence'])
                else:
                    outcome = self.pool.review_portrait(record['id'], digest, style, result.get('medium'))
                with self.lock:
                    self.state['reviewed'] += 1
                    if outcome.get('decision') in ('reject', 'duplicate'):
                        self.state['reviewRejected'] += 1
                        source = record.get('catalogueSource') or record.get('metricsSource')
                        if source in self.state['perSource']:
                            stat = self.state['perSource'][source]
                            stat['rejected'] = stat.get('rejected', 0) + 1
                        self._reason(result.get('reason') or ('去重：重复头像或设定' if outcome['decision'] == 'duplicate' else '头像不符合要求'))
                    self._save()
            except Exception as error:
                reason = '头像检查未完成：' + str(error)[:200]
                self._defer_record(record, reason)
                self._update(reviewDeferred=self.state['reviewDeferred'] + 1,
                             reviewReason=reason)
            if len(attempted) % 3 == 0:
                break
        index = getattr(self.pool, 'index_cover_embeddings', None)
        if callable(index) and not self.cancel_event.is_set():
            index(limit=3)

    def _defer_record(self, record, reason):
        defer = getattr(self.pool, 'defer_portraits', None)
        if callable(defer):
            try:
                defer([record['id']], reason=reason)
            except Exception as error:
                self._update(reviewReason='疑难头像暂未移出缓冲：' + str(error)[:200])

    @contextmanager
    def _collection_guard(self):
        lock = getattr(self.pool, 'collection_lock', None)
        if lock is None:
            yield not self.cancel_event.is_set()
            return
        acquired = False
        while not self.cancel_event.is_set():
            if lock.acquire(timeout=0.2):
                acquired = True
                break
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def _known(self, item):
        with self.pool.lock:
            checker = getattr(self.pool, '_available', None)
            return not checker(item) if callable(checker) else item['url'] in self.pool.seen_keys

    def _source_error(self, source, error, catalogue=False):
        with self.lock:
            stat = self.state['perSource'][source]
            stat['errors'] += 1
            stat['error'] = str(error)[:300]
            stat['status'] = 'error'
            if catalogue:
                self.state['cursors'][source]['exhausted'] = True
            self._save()

    def _has_unattempted_reviews(self, records):
        if not self.state.get('reviewerAvailable'):
            return False
        attempted = set(self.state['reviewAttempts'])
        return any((r.get('avatarAsset') or {}).get('sha256') and
                   (r.get('avatarAsset') or {}).get('review') != 'approved' and
                   str(r.get('id')) + ':' + str(r['avatarAsset']['sha256']) not in attempted
                   for r in records['portraitPending'] + records['portraitDeferred'])

    def _next_item(self):
        # One catalogue request per source per pass. Empty/known-only pages do
        # not mean a source is exhausted when it returned a real nextPage.
        with self.lock:
            sources = self.state['sources'][:]
            turn = self.state['sourceTurn']
            offset = turn % len(sources)
            self.state['sourceTurn'] += 1
        sources = sources[offset:] + sources[:offset]
        preferred = [s for s in getattr(self.registry, 'nsfw_sources', ()) if s in sources]
        with self.pool.lock:
            count = sum(r.get('sourceRating') == 'NSFW' for r in self.pool.state.get('ready', []))
        percent = getattr(self.registry, 'target_nsfw_percent', 80)
        want_nsfw = count < (self.pool.target * percent + 50) // 100
        if preferred and want_nsfw:
            other = [s for s in sources if s not in preferred]
            if turn % 4 != 3 or not other:
                sources = preferred + other
            else:
                # Reserve every fourth item for another configured channel;
                # merely rotating all sources can repeatedly put Chub first.
                other = [s for s in self.state['sources'] if s not in preferred]
                explore = (turn // 4) % len(other)
                sources = other[explore:] + other[:explore] + preferred
        for source in sources:
            if self.cancel_event.is_set():
                return None
            with self.lock:
                cursor = self.state['cursors'][source]
            while True:
                with self.lock:
                    item = cursor['queue'].pop(0) if cursor['queue'] else None
                if item is None:
                    break
                if self._known(item):
                    with self.lock:
                        self.state['skippedKnown'] += 1
                else:
                    self._save()
                    return item
            with self.lock:
                skip = cursor['exhausted'] or self.state['catalogues'] >= self.state['catalogueLimit']
            if skip:
                continue
            self._update(currentStage='catalogue', currentSource=source)
            try:
                supports = getattr(self.registry, 'supports_query_override', False)
                supports = supports(source) if callable(supports) else supports
                taste = getattr(self.pool, 'taste', None)
                with self.lock:
                    turn = self.state['queryTurn']
                    self.state['queryTurn'] += 1
                query = taste.discovery_query(turn) if taste and supports else None
                if query and cursor.setdefault('queryExhausted', {}).get(query, 0) > time.time():
                    query = None
                # Count a failed catalogue against the same bounded budget.
                with self.lock:
                    page = cursor['queryPages'].get(query, 1) if query else cursor['page']
                    self.state['catalogues'] += 1
                    self.state['perSource'][source]['catalogues'] += 1
                    self._save()
                result = self.registry.catalogue(source, page, 20, query=query) if query else self.registry.catalogue(source, page, 20)
                if not isinstance(result, dict) or not isinstance(result.get('items'), list):
                    raise ValueError('来源目录未返回有效列表')
                rows = result['items']
                if any(not isinstance(v, dict) or not isinstance(v.get('url'), str) or not v['url'] for v in rows):
                    raise ValueError('来源目录包含无效链接')
                following = result.get('nextPage')
                with self.lock:
                    cursor['queue'] = sorted(({**v, 'source': source} for v in rows), key=lambda v: v.get('sourceRating') == 'NSFW', reverse=True)
                    if query:
                        cursor['queryPages'][query] = following or 1
                        if not following:
                            # Revisit completed searches for updates, not every click.
                            cursor['queryExhausted'][query] = time.time() + 21600
                        else:
                            cursor['queryExhausted'].pop(query, None)
                    else:
                        cursor['page'] = following or page
                        cursor['exhausted'] = not bool(following)
                    self.state['perSource'][source]['discovered'] += len(rows)
                    self._save()
                while True:
                    with self.lock:
                        item = cursor['queue'].pop(0) if cursor['queue'] else None
                    if item is None:
                        break
                    if self._known(item):
                        with self.lock:
                            self.state['skippedKnown'] += 1
                    else:
                        self._save()
                        return item
            except Exception as error:
                self._source_error(source, error, catalogue=True)
        self._update(currentStage='catalogue')
        return None

    def _read(self, item):
        source = item['source']
        self._update(currentStage='read', currentSource=source, currentName=item.get('name', ''))
        with self.lock:
            self.state['read'] += 1
            self.state['perSource'][source]['read'] += 1
            self._save()
        with self.pool.lock:
            self.pool.pending.add(item['url'])
        try:
            result = self.registry.read(item)
            if self.cancel_event.is_set():
                return
            if not isinstance(result, dict) or not isinstance(result.get('record'), dict):
                raise ValueError('来源未返回完整角色资料')
            with self.lock:
                self.state['readSucceeded'] += 1
                self.state['perSource'][source]['readSucceeded'] += 1
            accepted, reason, candidate = evaluate(result['record'])
            if accepted and getattr(self.pool, 'taste', None):
                decision, reason, _ = self.pool.taste.evaluate(candidate)
                accepted = decision != 'reject'
            if not accepted:
                self._reason(reason)
                with self.lock:
                    self.state['readRejected'] += 1
                    self.state['perSource'][source]['rejected'] = self.state['perSource'][source].get('rejected', 0) + 1
            # Shelf acceptance remains authoritative (including dedupe, image
            # download, age/definition filters and source-bound visual review).
            self.pool.accept(result)
            if accepted:
                with self.pool.lock:
                    recent = next((r for r in reversed(self.pool.state.get('retired', []))
                                   if r.get('source') == result['record'].get('source')), None)
                if recent:
                    self._reason(recent.get('retiredReason'))
                    with self.lock:
                        self.state['readRejected'] += 1
                        self.state['perSource'][source]['rejected'] = self.state['perSource'][source].get('rejected', 0) + 1
        except Exception as error:
            self._source_error(source, error)
            failure = getattr(self.pool, '_failure', None)
            if callable(failure):
                failure(item['url'], error)
        finally:
            with self.pool.lock:
                self.pool.pending.discard(item['url'])
            with self.lock:
                self._save()

    def _finish(self, status, message):
        self._reconcile()
        with self.lock:
            for stat in self.state['perSource'].values():
                if stat.get('mode') == 'full':
                    stat['status'] = 'error' if stat.get('errors') else ('waitingReview' if stat.get('pending') else status if status in ('cancelled', 'failed') else 'completed')
            self._update(active=False, status=status, finishedAt=now_iso(), currentStage='', message=message)
            paused = getattr(self.pool, 'collection_paused', None)
            if paused is not None:
                paused.clear()

    def _run(self):
        try:
            prepare = getattr(self.reviewer, 'prepare', None)
            if callable(prepare) and not self.cancel_event.is_set():
                self._update(currentStage='preparing_models', message='正在准备自动头像检查；首次运行可能需要下载模型。')
                try:
                    prepare(cancel_event=self.cancel_event)
                except Exception as error:
                    self._update(reviewerAvailable=False, reviewReason='自动头像检查准备未完成：' + str(error)[:200])
                self._update(message='正在读取各来源目录、完整资料，并检查重复、分级、年龄、热度和头像。')
            with self._collection_guard() as acquired:
                if acquired:
                    self._discover_metadata()
            empty_passes = 0
            while not self.cancel_event.is_set():
                with self._collection_guard() as acquired:
                    if not acquired:
                        break
                    self._review_pending()
                    records = self._reconcile()
                    capacity = self.pool.target + self.pool.reserve_target
                    if len(records['portraitPending']) >= capacity:
                        if self._has_unattempted_reviews(records):
                            continue
                        if any((r.get('avatarAsset') or {}).get('review') != 'approved'
                               for r in records['portraitPending']):
                            self._finish('waitingReview', '候选缓冲已满；请处理待检查头像，或配置自动头像检查后再次读取。')
                        else:
                            self._finish('completed', '候选缓冲已满；已核验角色会在点击“已使用”后自动补位。')
                        return
                    if self.state['read'] >= self.state['maxRead']:
                        if self._has_unattempted_reviews(records):
                            continue
                        break
                    item = self._next_item()
                    if item is not None:
                        empty_passes = 0
                        self._read(item)
                        self._review_pending()
                        self._reconcile()
                    else:
                        empty_passes += 1
                        all_done = all(c['exhausted'] and not c['queue'] for c in self.state['cursors'].values())
                        if all_done or self.state['catalogues'] >= self.state['catalogueLimit'] or empty_passes > self.state['catalogueLimit']:
                            break
                if self.cancel_event.wait(0.01):
                    break
            # Finish reviewing existing candidates even when remote catalogues
            # have ended or failed; networking must not strand local images.
            while not self.cancel_event.is_set():
                records = self._records()
                if not self._has_unattempted_reviews(records):
                    break
                before = len(self.state['reviewAttempts'])
                with self._collection_guard() as acquired:
                    if not acquired:
                        break
                    self._review_pending()
                    self._reconcile()
                if len(self.state['reviewAttempts']) == before:
                    break
            if self.cancel_event.is_set():
                self._finish('cancelled', '已停止读取；已取得的资料和使用记录均已保留。')
            else:
                self._reconcile()
                if self.state['pending'] and not self.state['reviewerAvailable']:
                    self._finish('waitingReview', '本批资料读取结束；仍有头像需要确认，确认后可入库或等待补位。')
                elif self.state['readSucceeded'] == 0 and any(s['errors'] for s in self.state['perSource'].values()):
                    self._finish('failed', '本批来源暂未取得完整资料；请查看各来源错误后重试。')
                else:
                    self._finish('completed', '本批自动筛选已完成；确定符合的已入库或等待补位，疑难头像保留待确认。'
                                 if self.state['pending'] else '本批自动读取已完成；符合要求的角色已入库或排队等待补位。')
        except Exception as error:
            self._finish('failed', '自动读取中断：' + str(error)[:250])


# A descriptive singular alias is convenient for embedders.
AutomationJobController = AutomationJobs
