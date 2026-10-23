"""Persistent type exclusions exercise the real taste and shelf boundaries."""
import copy
import tempfile
import threading
import unittest
from unittest.mock import patch

from pool_store import PoolStore
from taste_profile import TasteProfile
from test_preference_learning import FakeCatalogue, role


GOTH = '哥特与冷感反差'
ROOMMATE = '室友与同居'


class TypeExclusionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.taste = TasteProfile(self.temp.name)
        self.pool = PoolStore(self.temp.name, FakeCatalogue(), target=5,
                              reserve_target=2, taste=self.taste)

    def tearDown(self):
        self.pool.close()
        self.temp.cleanup()

    def reviewed(self, key, themes=(GOTH,), style='detailed_illustration',
                 medium='illustration', source=None):
        record = role(key, style=style, medium=medium)
        tags = ['Adult', 'Female', 'Original']
        if GOTH in themes:
            tags.append('Goth')
        if ROOMMATE in themes:
            tags.append('Roommate')
        record['category'] = ', '.join(tags)
        record['toneMatches'] = list(themes)
        if source:
            record['source'] = source
        self.taste.review(record, style, medium)
        return record

    def live_ids(self):
        return {r['id'] for group in ('ready', 'reserve', 'portraitPending',
                                     'portraitDeferred')
                for r in self.pool.state.get(group, [])}

    def test_one_dislike_persists_a_type_and_blocks_independent_cross_site_role(self):
        rejected = self.reviewed('first')
        similar = self.reviewed('other', source='https://another.example/other')
        self.taste.feedback(rejected, 'less')
        self.assertIn('typeRule', self.taste.state['feedback'][rejected['source']])
        decision, reason, _ = self.taste.evaluate(similar)
        self.assertEqual(decision, 'reject')
        self.assertTrue(reason.startswith('已按你的标注排除类型：'))
        self.assertTrue(self.taste.model()['blockedTypes'])
        unrelated = self.reviewed('roommate', themes=(ROOMMATE,))
        self.assertEqual(self.taste.evaluate(unrelated)[0], 'accept')

    def test_combined_themes_require_all_features_instead_of_independent_or_rules(self):
        self.taste.feedback(self.reviewed('goth-roommate', (GOTH, ROOMMATE)), 'less')
        self.assertEqual(self.taste.evaluate(self.reviewed('only-goth'))[0], 'accept')
        self.assertEqual(self.taste.evaluate(self.reviewed('only-roommate', (ROOMMATE,)))[0], 'accept')
        self.assertEqual(self.taste.evaluate(self.reviewed('both', (GOTH, ROOMMATE)))[0], 'reject')

    def test_generic_visuals_and_gender_never_independently_exclude_other_types(self):
        self.taste.feedback(self.reviewed('goth'), 'less')
        rule = self.taste.state['feedback']['https://example.org/goth']['typeRule']
        self.assertFalse(set(rule['features']).intersection({
            'style:portrait', 'style:detailed_illustration', 'medium:illustration',
            'gender:Female', 'gender:Male', 'tag:adult', 'tag:original',
        }))
        self.assertEqual(self.taste.evaluate(self.reviewed('same-visual-other-type', (ROOMMATE,)))[0], 'accept')

    def test_specific_character_tags_work_without_a_known_theme_or_portrait(self):
        first, similar = role('first'), role('other')
        for record in (first, similar):
            record['toneMatches'] = []
            record['category'] = 'Adult, Female, Knight'
            record.pop('avatarAsset')
        self.taste.feedback(first, 'less')
        self.assertEqual(self.taste.model()['blockedTypes'][0]['features'], ['type:knight'])
        self.assertEqual(self.taste.evaluate(similar)[0], 'reject')

    def test_unknown_type_never_becomes_a_blanket_generic_avatar_ban(self):
        first = self.reviewed('unspecified', themes=())
        self.taste.feedback(first, 'less')
        self.assertEqual(self.taste.model()['blockedTypes'], [])
        self.assertEqual(self.taste.evaluate(self.reviewed('roommate', (ROOMMATE,)))[0], 'accept')

    def test_bad_avatar_style_vote_does_not_ban_its_unrelated_story_type(self):
        cartoon = self.reviewed('cartoon', style='chibi_cartoon')
        self.taste.feedback(cartoon, 'less')
        rule = self.taste.state['feedback'][cartoon['source']]['typeRule']
        self.assertEqual(rule['features'], ['style:chibi_cartoon'])
        self.assertEqual(self.taste.evaluate(self.reviewed('detailed-goth'))[0], 'accept')

    def test_type_exclusion_survives_taste_restart(self):
        self.taste.feedback(self.reviewed('voted'), 'less')
        future = self.reviewed('future')
        restarted = TasteProfile(self.temp.name)
        self.assertEqual(restarted.evaluate(future)[0], 'reject')
        self.assertTrue(restarted.model()['blockedTypes'])

    def test_legacy_dislike_is_not_silently_upgraded_to_a_type_rule(self):
        old = self.reviewed('old')
        self.taste.feedback(old, 'less')
        self.taste.state['feedback'][old['source']].pop('typeRule', None)
        self.taste._model = None
        self.taste._save()
        self.taste.hydrate_feedback([old])
        future = self.reviewed('new')
        restarted = TasteProfile(self.temp.name)
        self.assertEqual(restarted.evaluate(old)[0], 'reject')
        self.assertEqual(restarted.evaluate(future)[0], 'accept')
        self.assertEqual(restarted.model()['blockedTypes'], [])

    def test_more_removes_own_rule_but_cannot_bypass_an_independent_rule(self):
        first, second = self.reviewed('first'), self.reviewed('second')
        self.taste.feedback(first, 'less')
        self.taste.feedback(second, 'less')
        self.taste.feedback(first, 'more')
        self.assertNotIn('typeRule', self.taste.state['feedback'][first['source']])
        self.assertEqual(self.taste.evaluate(first)[0], 'reject')
        self.taste.feedback(second, 'clear')
        self.assertEqual(self.taste.evaluate(first)[0], 'accept')
        self.assertEqual(self.taste.evaluate(self.reviewed('new'))[0], 'accept')

    def test_clear_removes_whole_mirror_identity_cluster(self):
        original = self.reviewed('original')
        self.taste.feedback(original, 'less')
        mirror = copy.deepcopy(original)
        mirror.update(source='https://mirror.example/renamed', id='mirror', name='Renamed')
        self.taste.feedback(mirror, 'less')
        self.taste.feedback(original, 'clear')
        self.assertEqual(self.taste.model()['blockedTypes'], [])
        self.assertEqual(self.taste.evaluate(self.reviewed('fresh'))[0], 'accept')

    def test_dislike_filters_all_current_inventory_groups_without_consumption(self):
        first = self.reviewed('voted')
        related = [self.reviewed('ready'), self.reviewed('reserve'),
                   self.reviewed('pending'), self.reviewed('deferred')]
        unrelated = self.reviewed('keep-roommate', (ROOMMATE,))
        self.pool.state.update(ready=[first, related[0], unrelated],
                               reserve=[related[1]], portraitPending=[related[2]],
                               portraitDeferred=[related[3]])
        self.pool.feedback(first['id'], 'less')
        self.assertEqual(self.live_ids(), {unrelated['id']})
        self.assertEqual(self.pool.snapshot()['usedCount'], 0)
        self.assertTrue({r['id'] for r in related}.issubset(
            {r['id'] for r in self.pool.state['retired']}))

    def test_undo_restores_other_type_retired_candidates_and_keeps_used_history(self):
        first, similar, used = (self.reviewed(k) for k in ('first', 'similar', 'used'))
        used['usedAt'] = '2026-10-22T00:00:00Z'
        self.pool.state.update(ready=[first, similar], history=[copy.deepcopy(used)])
        before = copy.deepcopy(self.pool.state['history'])
        self.pool.feedback(first['id'], 'less')
        self.assertNotIn(similar['id'], self.live_ids())
        self.pool.feedback(first['id'], 'clear')
        self.assertIn(first['id'], self.live_ids())
        self.assertIn(similar['id'], self.live_ids())
        self.assertEqual(self.pool.state['history'], before)
        self.assertEqual(self.pool.snapshot()['usedCount'], 1)

    def test_clearing_one_of_two_independent_dislikes_does_not_restore_the_type(self):
        first, second, similar = (self.reviewed(k) for k in ('first', 'second', 'similar'))
        self.pool.state.update(ready=[first, second, similar])
        self.pool.feedback(first['id'], 'less')
        self.pool.feedback(second['id'], 'less')
        self.pool.feedback(first['id'], 'clear')
        self.assertNotIn(similar['id'], self.live_ids())
        self.pool.feedback(second['id'], 'clear')
        self.assertIn(similar['id'], self.live_ids())
        self.assertEqual(self.pool.snapshot()['usedCount'], 0)

    def test_new_rule_during_avatar_download_prevents_inflight_candidate_committing(self):
        first, candidate = self.reviewed('first'), self.reviewed('inflight')
        self.pool.state['ready'] = [first]
        asset = candidate.pop('avatarAsset')
        entered, release = threading.Event(), threading.Event()
        errors, results = [], []

        def fetch(record):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Test did not release fake avatar fetch')
            return b'fake image bytes', record['avatarSource']

        def run():
            try:
                results.append(self.pool.accept({'record': candidate}))
            except BaseException as error:
                errors.append(error)

        with patch('pool_store.fetch_record_avatar', side_effect=fetch), \
                patch('pool_store.cache_avatar', return_value=asset):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(3), 'Read did not reach the avatar request')
                self.pool.feedback(first['id'], 'less')
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [False])
        self.assertFalse(any(r['source'] == candidate['source']
                             for group in ('ready', 'reserve', 'portraitPending', 'portraitDeferred')
                             for r in self.pool.state.get(group, [])))
        self.assertEqual(self.pool.snapshot()['usedCount'], 0)

    def test_rule_added_after_portrait_evaluation_is_rechecked_inside_commit_lock(self):
        first, candidate = self.reviewed('first'), self.reviewed('last-window')
        self.pool.state['ready'] = [first]
        asset = candidate.pop('avatarAsset')
        evaluated, release = threading.Event(), threading.Event()
        original_evaluate = self.taste.evaluate
        calls, errors, results = [], [], []

        def evaluate(record):
            result = original_evaluate(record)
            if record['source'] == candidate['source']:
                calls.append(result[0])
                if len(calls) == 2:
                    evaluated.set()
                    if not release.wait(5):
                        raise TimeoutError('Test did not release the stale evaluation')
            return result

        def run():
            try:
                results.append(self.pool.accept({'record': candidate, 'image': b'fake'}))
            except BaseException as error:
                errors.append(error)

        with patch.object(self.taste, 'evaluate', side_effect=evaluate), \
                patch('pool_store.cache_avatar', return_value=asset):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(evaluated.wait(3), 'Read did not finish portrait evaluation')
                self.pool.feedback(first['id'], 'less')
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [False])
        self.assertFalse(any(r['source'] == candidate['source']
                             for group in ('ready', 'reserve', 'portraitPending', 'portraitDeferred')
                             for r in self.pool.state.get(group, [])))
        self.assertEqual(self.pool.snapshot()['usedCount'], 0)

    def test_new_card_rejected_before_avatar_download_can_return_after_undo(self):
        first, candidate = self.reviewed('first'), self.reviewed('newly-filtered')
        self.pool.state['ready'] = [first]
        asset = candidate.pop('avatarAsset')
        self.pool.feedback(first['id'], 'less')
        with patch('pool_store.fetch_record_avatar') as fetch:
            self.assertFalse(self.pool.accept({'record': candidate}))
            fetch.assert_not_called()
        self.assertTrue(any(r['source'] == candidate['source']
                            for r in self.pool.state['retired']))
        self.pool.feedback(first['id'], 'clear')
        self.assertTrue(any(r['source'] == candidate['source']
                            for group in ('ready', 'reserve', 'portraitPending', 'portraitDeferred')
                            for r in self.pool.state.get(group, [])))
        asset['review'] = 'pending'
        with patch('pool_store.fetch_record_avatar', return_value=(b'image', candidate['avatarSource'])) as fetch, \
                patch('pool_store.cache_avatar', return_value=asset):
            self.pool.sync_avatars()
        self.assertEqual(sum(call.args[0]['source'] == candidate['source'] for call in fetch.call_args_list), 1)
        restored = next(r for r in self.pool.state['portraitPending'] if r['source'] == candidate['source'])
        self.assertEqual(restored['avatarAsset']['sha256'], asset['sha256'])
        self.assertEqual(restored['avatarAsset']['review'], 'pending')
        self.assertEqual(self.pool.snapshot()['usedCount'], 0)


if __name__ == '__main__':
    unittest.main()
