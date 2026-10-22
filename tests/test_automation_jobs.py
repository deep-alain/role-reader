import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from automation_jobs import AutomationJobs
from pool_policy import evaluate
from pool_sources import card_to_record


def record(key, source='good', age=28):
    role = card_to_record({
        'name': 'Adult roommate ' + key, 'age': age,
        'description': ('A thoughtful adult roommate with an independent life, work, friends, '
                        'a quiet sense of humour and clear personal boundaries. ') * 4 + key,
        'first_mes': 'Your roommate puts a warm cup on the table, smiles, and asks how your evening went. ' + key,
        'scenario': 'A shared apartment in the evening.', 'tags': ['Adult', 'Roommate'],
    }, 'https://example.org/' + key, 'https://example.org/' + key + '.png')
    role.update(id=key, catalogueSource=source, avatarSource=role['avatar'],
                avatarAsset={'sha256': 'digest-' + key, 'source': role['avatar'], 'review': 'pending'})
    return role


class FakePool:
    def __init__(self, client, target=3, reserve_target=0, taste=None):
        self.client = client
        self.target = target
        self.reserve_target = reserve_target
        self.taste = taste
        self.lock = threading.RLock()
        self.collection_lock = threading.RLock()
        self.pending = set()
        self.seen_keys = set()
        self.state = {'ready': [], 'reserve': [], 'portraitPending': [], 'history': [], 'retired': []}
        self.events = []

    def _available(self, item):
        return item['url'] not in self.seen_keys and item['url'] not in self.pending

    def accept(self, result):
        row = copy.deepcopy(result['record'])
        with self.lock:
            if row['source'] in self.seen_keys:
                return False
            self.seen_keys.add(row['source'])
            if not evaluate(row)[0]:
                return False
            self.events.append('accept:' + row['id'])
            if self.taste:
                self.state['portraitPending'].append(row)
                return False
            row['avatarAsset']['review'] = 'approved'
            if len(self.state['ready']) < self.target:
                self.state['ready'].append(row)
                return True
            self.state['portraitPending'].append(row)
            return False

    def review_portrait(self, role_id, digest, style, medium=None):
        with self.lock:
            row = next(r for r in self.state['portraitPending'] if r['id'] == role_id)
            if row['avatarAsset']['sha256'] != digest:
                raise ValueError('stale image')
            self.events.append('review:' + role_id)
            row['avatarAsset']['review'] = 'approved'
            if style == 'other_rejected':
                self.state['portraitPending'].remove(row)
                self.state['retired'].append({**row, 'retiredReason': '头像不符合要求'})
                return {'decision': 'reject'}
            if len(self.state['ready']) < self.target:
                self.state['portraitPending'].remove(row)
                self.state['ready'].append(row)
            return {'decision': 'accept'}


class FakeRegistry:
    sources = ('good',)

    def __init__(self, count=3):
        self.count = count
        self.catalogue_calls = []
        self.reads = []
        self.started = threading.Event()
        self.release = None
        self.pool = None

    def supports_query_override(self, source):
        return False

    def catalogue(self, source, page=1, limit=20):
        self.catalogue_calls.append((source, page))
        if source == 'bad':
            raise ValueError('source requires authorization')
        rows = [{'source': source, 'url': 'https://example.org/' + str(i), 'key': str(i)}
                for i in range((page - 1) * 2, min(page * 2, self.count))]
        return {'items': rows, 'nextPage': page + 1 if page * 2 < self.count else None}

    def read(self, item):
        self.started.set()
        if self.release:
            self.release.wait(3)
        key = item['key']
        self.reads.append(key)
        if self.pool and hasattr(self.pool, 'events'):
            self.pool.events.append('read:' + key)
        return {'record': record(key, item['source'])}


class Taste:
    def evaluate(self, row):
        return 'pending', '头像待核验', row

    def discovery_query(self, turn):
        return None


class AutomationJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.close()
        self.temp.cleanup()

    def controller(self, client=None, pool=None, **options):
        client = client or FakeRegistry()
        pool = pool or FakePool(client)
        client.pool = pool
        controller = AutomationJobs(self.temp.name, pool, client, **options)
        self.controllers.append(controller)
        return controller, pool, client

    def wait_done(self, controller):
        deadline = time.monotonic() + 4
        while controller.snapshot()['job']['active'] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(controller.snapshot()['job']['active'])
        return controller.snapshot()['job']

    def test_lifecycle_bounded_read_persistent_progress_and_no_consumption(self):
        controller, pool, client = self.controller()
        started = controller.start(max_read=2)['job']
        job = self.wait_done(controller)
        self.assertEqual(started['id'], job['id'])
        self.assertEqual((job['status'], job['read'], job['accepted'], job['readyAdded']), ('completed', 2, 2, 2))
        self.assertEqual(pool.state['history'], [])
        self.assertEqual(job['perSource']['good']['read'], 2)
        saved = json.loads(controller.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['id'], job['id'])
        self.assertEqual(saved['read'], 2)
        self.assertIn('queue', saved['cursors']['good'])
        self.assertNotIn('queue', job['cursors']['good'])

    def test_source_failure_does_not_stop_other_full_sources(self):
        client = FakeRegistry(2)
        client.sources = ('bad', 'good')
        controller, pool, _ = self.controller(client)
        controller.start(2)
        job = self.wait_done(controller)
        self.assertEqual(job['accepted'], 2)
        self.assertEqual(job['status'], 'completed')
        self.assertIn('authorization', job['perSource']['bad']['error'])
        self.assertEqual(job['perSource']['bad']['read'], 0)
        self.assertEqual(job['perSource']['good']['read'], 2)

    def test_duplicate_start_and_cooperative_cancel_do_not_accept_late_response(self):
        client = FakeRegistry(5)
        client.release = threading.Event()
        controller, pool, _ = self.controller(client)
        first = controller.start(5)
        self.assertTrue(client.started.wait(1))
        second = controller.start(1)
        self.assertEqual(first['job']['id'], second['job']['id'])
        self.assertTrue(second['alreadyRunning'])
        controller.cancel()
        client.release.set()
        job = self.wait_done(controller)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(job['read'], 1)
        self.assertEqual(job['accepted'], 0)
        self.assertEqual(pool.state['ready'], [])
        self.assertFalse(pool.pending)

    def test_known_head_pages_do_not_prevent_paging_to_new_records(self):
        client = FakeRegistry(4)
        pool = FakePool(client)
        pool.seen_keys.update(('https://example.org/0', 'https://example.org/1'))
        controller, _, _ = self.controller(client, pool)
        controller.start(2)
        job = self.wait_done(controller)
        self.assertEqual(client.reads, ['2', '3'])
        self.assertIn(('good', 2), client.catalogue_calls)
        self.assertEqual(job['skippedKnown'], 2)

    def test_reviews_are_interleaved_and_approved_future_candidates_count_on_full_shelf(self):
        client = FakeRegistry(2)
        pool = FakePool(client, target=3, taste=Taste())
        pool.state['ready'] = [record('existing-' + str(i)) for i in range(3)]
        controller, _, _ = self.controller(client, pool, portrait_reviewer=lambda r: {'style': 'photo', 'medium': 'photorealistic'})
        controller.start(2)
        job = self.wait_done(controller)
        self.assertLess(pool.events.index('review:0'), pool.events.index('read:1'))
        self.assertEqual((job['accepted'], job['readyAdded'], job['futureReady']), (2, 0, 2))
        self.assertEqual([r['id'] for r in pool.state['ready']], ['existing-0', 'existing-1', 'existing-2'])
        self.assertEqual(pool.state['history'], [])

    def test_full_review_buffer_reports_wait_without_reading_or_spinning(self):
        client = FakeRegistry(5)
        pool = FakePool(client, target=1, taste=Taste())
        pool.state['portraitPending'] = [record('waiting')]
        controller, _, _ = self.controller(client, pool)
        controller.start(5)
        job = self.wait_done(controller)
        self.assertEqual(job['status'], 'waitingReview')
        self.assertEqual(job['pending'], 1)
        self.assertEqual(job['read'], 0)
        self.assertEqual(client.catalogue_calls, [])
        self.assertFalse(job['reviewerAvailable'])

    def test_uncertain_image_continues_until_bounded_batch_or_buffer_is_full(self):
        client = FakeRegistry(3)
        pool = FakePool(client, target=4, taste=Taste())
        controller, _, _ = self.controller(client, pool, portrait_reviewer=lambda r: {'decision': 'defer', 'reason': 'uncertain'})
        controller.start(3)
        job = self.wait_done(controller)
        self.assertEqual(job['read'], 3)
        self.assertEqual(job['reviewDeferred'], 3)
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['accepted'], 0)

    def test_full_buffer_with_available_reviewer_checks_all_candidates_before_stopping(self):
        client = FakeRegistry(2)
        pool = FakePool(client, target=7, taste=Taste())
        pool.state['ready'] = [record('existing-' + str(i)) for i in range(7)]
        pool.state['portraitPending'] = [record('waiting-' + str(i)) for i in range(7)]
        controller, _, _ = self.controller(client, pool, portrait_reviewer=lambda r: {'style': 'photo'})
        controller.start(2)
        job = self.wait_done(controller)
        self.assertEqual(job['reviewed'], 7)
        self.assertEqual(job['accepted'], 7)
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['pending'], 0)
        self.assertEqual(job['read'], 0)

    def test_deferred_old_portraits_do_not_block_new_reads_or_inflate_approved_count(self):
        class DeferringPool(FakePool):
            def defer_portraits(self, record_ids, reason='uncertain'):
                with self.lock:
                    moved = [r for r in self.state['portraitPending'] if r['id'] in record_ids
                             and r['avatarAsset'].get('review') != 'approved']
                    self.state.setdefault('portraitDeferred', []).extend(moved)
                    self.state['portraitPending'] = [r for r in self.state['portraitPending'] if r not in moved]
        client = FakeRegistry(2)
        pool = DeferringPool(client, target=7, taste=Taste())
        pool.state['ready'] = [record('existing-' + str(i)) for i in range(7)]
        pool.state['portraitPending'] = [record('waiting-' + str(i)) for i in range(7)]
        def reviewer(row):
            return {'decision': 'defer', 'reason': 'uncertain'} if row['id'].startswith('waiting-') else {'style': 'photo'}
        controller, _, _ = self.controller(client, pool, portrait_reviewer=reviewer)
        controller.start(2)
        job = self.wait_done(controller)
        self.assertEqual((job['read'], job['accepted'], job['deferred'], job['pending']), (2, 2, 7, 7))
        self.assertEqual(job['perSource']['good']['pending'], 7)
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(len(pool.state['portraitDeferred']), 7)
        self.assertEqual(pool.state['history'], [])

    def test_cancel_after_deferring_old_buffer_still_discards_late_read(self):
        class DeferringPool(FakePool):
            def defer_portraits(self, record_ids, reason='uncertain'):
                with self.lock:
                    moved = [r for r in self.state['portraitPending'] if r['id'] in record_ids]
                    self.state.setdefault('portraitDeferred', []).extend(moved)
                    self.state['portraitPending'] = [r for r in self.state['portraitPending'] if r not in moved]
        client = FakeRegistry(2)
        client.release = threading.Event()
        pool = DeferringPool(client, target=2, taste=Taste())
        pool.state['portraitPending'] = [record('waiting-0'), record('waiting-1')]
        controller, _, _ = self.controller(client, pool, portrait_reviewer=lambda r: {'decision': 'defer'})
        controller.start(2)
        self.assertTrue(client.started.wait(1))
        controller.cancel()
        client.release.set()
        job = self.wait_done(controller)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(job['deferred'], 2)
        self.assertEqual(job['read'], 1)
        self.assertEqual(job['accepted'], 0)
        self.assertEqual(pool.state['history'], [])

    def test_later_job_can_approve_quarantined_portraits_without_consuming_stock(self):
        class RevisitablePool(FakePool):
            def defer_portraits(self, record_ids, reason='uncertain'):
                with self.lock:
                    moved = [r for r in self.state['portraitPending'] if r['id'] in record_ids]
                    self.state.setdefault('portraitDeferred', []).extend(moved)
                    self.state['portraitPending'] = [r for r in self.state['portraitPending'] if r not in moved]

            def review_portrait(self, role_id, digest, style, medium=None):
                with self.lock:
                    deferred = self.state.setdefault('portraitDeferred', [])
                    row = next((r for r in deferred if r['id'] == role_id), None)
                    if row:
                        deferred.remove(row)
                        self.state['portraitPending'].append(row)
                    return super().review_portrait(role_id, digest, style, medium)
        client = FakeRegistry(0)
        pool = RevisitablePool(client, target=2, taste=Taste())
        pool.state['ready'] = [record('existing-0'), record('existing-1')]
        pool.state['portraitPending'] = [record('waiting-0'), record('waiting-1')]
        controller, _, _ = self.controller(client, pool, portrait_reviewer=lambda r: {'decision': 'defer'})
        controller.start(1)
        first = self.wait_done(controller)
        self.assertEqual((first['accepted'], first['deferred']), (0, 2))
        controller.reviewer = lambda r: {'style': 'photo'}
        controller.start(1)
        second = self.wait_done(controller)
        self.assertEqual((second['accepted'], second['deferred'], second['futureReady']), (2, 0, 2))
        self.assertEqual([r['id'] for r in pool.state['ready']], ['existing-0', 'existing-1'])
        self.assertEqual(pool.state['history'], [])

    def test_model_preparation_runs_in_worker_and_defers_on_unavailable(self):
        class Reviewer:
            def __init__(self):
                self.prepared = None
                self.cancel_event = None

            def available(self):
                return False

            def prepare(self, cancel_event=None):
                self.prepared = threading.current_thread()
                self.cancel_event = cancel_event

            def __call__(self, row):
                raise AssertionError('unavailable reviewer should never be called')
        reviewer = Reviewer()
        client = FakeRegistry(1)
        controller, _, _ = self.controller(client, FakePool(client, taste=Taste()), portrait_reviewer=reviewer)
        controller.start(1)
        job = self.wait_done(controller)
        self.assertIsNot(reviewer.prepared, threading.current_thread())
        self.assertIs(reviewer.cancel_event, controller.cancel_event)
        self.assertEqual(job['status'], 'waitingReview')
        self.assertEqual(job['read'], 1)

    def test_metadata_discovery_is_separate_and_never_counts_as_read_or_acceptance(self):
        controller, _, _ = self.controller(FakeRegistry(0), discovery=lambda s: {
            'items': [{'id': s + '-1', 'name': 'Catalogue entry', 'url': 'https://example.org/' + s,
                       'description': 'do not persist bulk definitions'}],
            'status': 'authorization_required', 'note': 'Complete definition needs normal login.'})
        controller.start(1)
        job = self.wait_done(controller)
        self.assertEqual((job['metadataFound'], job['read'], job['accepted']), (2, 0, 0))
        self.assertEqual(job['perSource']['spicychat']['mode'], 'metadata')
        saved = json.loads((Path(self.temp.name) / 'automation_candidates.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['items']), 2)
        self.assertNotIn('description', saved['items'][0])

    def test_policy_rejection_has_reason_without_fabricated_profile(self):
        client = FakeRegistry(1)
        client.read = lambda item: {'record': record('minor', age=16)}
        controller, pool, _ = self.controller(client)
        controller.start(1)
        job = self.wait_done(controller)
        self.assertEqual(job['readSucceeded'], 1)
        self.assertEqual(job['accepted'], 0)
        self.assertEqual(job['rejected'], 1)
        self.assertEqual(job['reasonCounts']['age'], 1)
        self.assertEqual(pool.state['ready'], [])

    def test_interrupted_progress_is_honestly_reported_after_restart(self):
        path = Path(self.temp.name) / 'automation_jobs.json'
        path.write_text(json.dumps({'version': 1, 'status': 'running', 'active': True, 'id': 'old', 'read': 7}), encoding='utf-8')
        controller, _, _ = self.controller()
        job = controller.snapshot()['job']
        self.assertEqual(job['status'], 'interrupted')
        self.assertEqual(job['read'], 7)
        self.assertFalse(job['active'])

    def test_input_limits_and_custom_adapters_do_not_get_query_arguments(self):
        controller, _, client = self.controller()
        for value in (0, 501, True, '120'):
            with self.assertRaises(ValueError):
                controller.start(value)
        with self.assertRaises(ValueError):
            controller.start(1, ['unconfigured'])
        with self.assertRaises(ValueError):
            controller.start(1, [{}])
        controller.start(1, ['good'])
        self.assertEqual(self.wait_done(controller)['read'], 1)
        self.assertEqual(client.catalogue_calls, [('good', 1)])

    def test_full_reads_hold_collection_lock_without_holding_shelf_state_lock(self):
        client = FakeRegistry(1)
        pool = FakePool(client)
        original_read = client.read
        checked = []
        def reader(item):
            checked.append(pool.collection_lock._is_owned())
            checked.append(not pool.lock._is_owned())
            return original_read(item)
        client.read = reader
        controller, _, _ = self.controller(client, pool)
        controller.start(1)
        self.wait_done(controller)
        self.assertEqual(checked, [True, True])

    def test_nsfw_sources_get_three_of_four_reads_while_other_channels_are_explored(self):
        class MixedRegistry(FakeRegistry):
            sources = ('plain', 'nsfw')
            nsfw_sources = ('nsfw',)
            target_nsfw_percent = 80

            def catalogue(self, source, page=1, limit=20):
                return {'items': [{'source': source, 'url': 'https://example.org/' + source + str(i),
                                   'key': source + str(i)} for i in range(10)], 'nextPage': None}
        client = MixedRegistry(10)
        pool = FakePool(client, target=6)
        controller, _, _ = self.controller(client, pool)
        controller.start(4)
        job = self.wait_done(controller)
        self.assertEqual(job['read'], 4)
        self.assertEqual([key.startswith('nsfw') for key in client.reads], [True, True, True, False])

    def test_builtin_query_overrides_persist_their_own_page_cursor(self):
        class QueryRegistry(FakeRegistry):
            def supports_query_override(self, source):
                return True

            def catalogue(self, source, page=1, limit=20, query=None):
                self.query = query
                return super().catalogue(source, page, limit)
        class QueryTaste(Taste):
            def discovery_query(self, turn):
                return 'roommate'
        client = QueryRegistry(4)
        pool = FakePool(client, target=5, taste=QueryTaste())
        controller, _, _ = self.controller(client, pool)
        controller.start(3)
        job = self.wait_done(controller)
        self.assertEqual(client.query, 'roommate')
        self.assertIn(('good', 2), client.catalogue_calls)
        saved = json.loads(controller.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['cursors']['good']['page'], 1)
        self.assertEqual(saved['cursors']['good']['queryPages']['roommate'], 1)
        self.assertEqual(job['read'], 3)

    def test_real_shelf_acceptance_is_used_and_survives_restart(self):
        from pool_store import PoolStore
        client = FakeRegistry(1)
        pool = PoolStore(self.temp.name, client, target=1, reserve_target=0)
        controller, _, _ = self.controller(client, pool)
        try:
            controller.start(1)
            job = self.wait_done(controller)
            self.assertEqual(job['accepted'], 1)
            self.assertEqual(pool.snapshot()['readyCount'], 1)
            self.assertEqual(pool.snapshot()['usedCount'], 0)
            controller.close()
            pool.close()
            restored = PoolStore(self.temp.name, client, target=1, reserve_target=0)
            try:
                self.assertEqual(restored.snapshot()['readyCount'], 1)
                self.assertEqual(restored.snapshot()['usedCount'], 0)
            finally:
                restored.close()
        finally:
            controller.close()
            pool.close()


if __name__ == '__main__':
    unittest.main()
