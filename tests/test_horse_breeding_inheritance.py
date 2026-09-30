"""Deterministic P6 breeding inheritance tests using only temporary SQLite."""
import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select, update

from dzmm_bot.application.horse_service import HorseService
from dzmm_bot.application.services import GameService
from dzmm_bot.core import Inbound
from dzmm_bot.domain.affinity_inheritance import inherit_foal_affinities
from dzmm_bot.domain.horse_catalog import foal_catalog
from dzmm_bot.domain.horse_racing import AFFINITY_KEYS
from dzmm_bot.domain.horse_rules import TRAITS, foal_snapshot
from dzmm_bot.persistence.schema import horse_affinities, horse_pregnancies, horses
from dzmm_bot.persistence.transport import rooms
from dzmm_bot.store import Store, outbox


class FoalRuleTests(unittest.TestCase):
    def test_stats_are_uniformly_drawn_inside_parent_range(self):
        mother = {**{trait: 200 for trait in TRAITS}, **{'name': '母云'}}
        father = {**{trait: 100 for trait in TRAITS}, **{'name': '父风'}}
        mother.update({f'growth_{trait}': 'B' for trait in TRAITS})
        father.update({f'growth_{trait}': 'A' for trait in TRAITS})
        first = foal_snapshot('fixed-foal-seed', mother, father)
        self.assertEqual(first, foal_snapshot('fixed-foal-seed', mother, father))
        for trait in TRAITS:
            self.assertEqual(first['trait_ranges'][trait], [100, 240])
            self.assertGreaterEqual(first['traits'][trait], 100)
            self.assertLessEqual(first['traits'][trait], 240)

    def test_parent_name_halves_are_combined_and_frozen(self):
        mother = {**{trait: 100 for trait in TRAITS}, 'name': '母云'}
        father = {**{trait: 100 for trait in TRAITS}, 'name': '父风'}
        mother.update({f'growth_{trait}': 'B' for trait in TRAITS})
        father.update({f'growth_{trait}': 'A' for trait in TRAITS})
        name = foal_snapshot('name-seed', mother, father)['name']
        self.assertIn(name, {'父云', '母风'})

    def test_inheritance_range_uses_high_parent_times_one_point_two(self):
        mother = {**{trait: 1000 for trait in TRAITS}, 'name': '母马'}
        father = {**{trait: 900 for trait in TRAITS}, 'name': '父马'}
        mother.update({f'growth_{trait}': 'S' for trait in TRAITS})
        father.update({f'growth_{trait}': 'A' for trait in TRAITS})
        snapshot = foal_snapshot('high-stats-seed', mother, father)
        for trait in TRAITS:
            self.assertEqual(snapshot['trait_ranges'][trait], [900, 1200])
            self.assertLessEqual(snapshot['traits'][trait], 1200)

    def test_affinity_inherits_parent_grade_and_improves_by_at_most_one(self):
        father = {category: {key: 'C' for key in keys} for category, keys in AFFINITY_KEYS.items()}
        mother = {category: {key: 'D' for key in keys} for category, keys in AFFINITY_KEYS.items()}
        snapshot = inherit_foal_affinities(father, mother, 'pregnancy', 'foal', 'father', 'mother')
        self.assertEqual(snapshot, inherit_foal_affinities(
            father, mother, 'pregnancy', 'foal', 'father', 'mother'))
        for category, keys in AFFINITY_KEYS.items():
            for key in keys:
                parent_grade = snapshot['inheritance_rolls'][category][key]['inherited_grade']
                result = snapshot['result'][category][key]
                self.assertIn(parent_grade, {'C', 'D'})
                self.assertIn(result, {parent_grade, 'SABCD'[max(0, 'SABCD'.index(parent_grade) - 1)]})

    def test_affinity_upgrade_never_exceeds_s(self):
        parent = {category: {key: 'S' for key in keys} for category, keys in AFFINITY_KEYS.items()}
        snapshot = inherit_foal_affinities(parent, parent, 'pregnancy-s', 'foal-s', 'f', 'm')
        self.assertTrue(all(grade == 'S' for values in snapshot['result'].values()
                            for grade in values.values()))

    def test_category_inheritance_supports_parent_or_dual_categories(self):
        father = {'recommended_distance': 'short', 'recommended_surface': 'turf',
                  'recommended_style': 'front'}
        mother = {'recommended_distance': 'mile', 'recommended_surface': 'dirt',
                  'recommended_style': 'closer'}
        dual_counts = {category: 0 for category in ('distance', 'surface', 'style')}
        for seed in range(1000):
            snapshot = foal_catalog(str(seed), father, mother)
            for category, field in (('distance', 'recommended_distance'),
                                    ('surface', 'recommended_surface'),
                                    ('style', 'recommended_style')):
                values = snapshot[field + '_options']
                self.assertTrue(set(values) <= {father[field], mother[field]})
                self.assertGreaterEqual(len(values), 1)
                self.assertLessEqual(len(values), 2)
                dual_counts[category] += len(values) == 2
        for count in dual_counts.values():
            self.assertGreater(count, 20)
            self.assertLess(count, 80)


class FoalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1790596800.0
        self.store = Store('sqlite:///' + str(Path(self.tmp.name) / 'breeding.db'),
                           secret='foal-test', clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert().values(id='room', kind='group', enabled=1))
        self.store.receive(Inbound(room='room', sender='player', name='玩家',
                                   message_id='register', text='/注册'))
        with self.store.engine.begin() as db:
            GameService(db, self.now, 'foal-test', 'player').ledger(
                5000, 'test_credit', 'test', 'foal-player-fund')
        for index in range(2):
            self.store.receive(Inbound(room='room', sender='player', name='玩家',
                                       message_id=f'buy-{index}', text='/买马'))
        with self.store.engine.begin() as db:
            rows = db.execute(select(horses).order_by(horses.c.created_at, horses.c.id)).mappings().all()
            parents = [dict(row) for row in rows]
            self.mother, self.father = parents[0], parents[1]
            db.execute(update(horses).where(horses.c.id == self.mother['id']).values(
                name='母云', sex='female', born_at=self.now - 8 * 3600,
                **{trait: 200 for trait in TRAITS}))
            db.execute(update(horses).where(horses.c.id == self.father['id']).values(
                name='父风', sex='male', born_at=self.now - 8 * 3600,
                **{trait: 100 for trait in TRAITS}))
            for parent, grade in ((self.mother, 'D'), (self.father, 'C')):
                db.execute(update(horse_affinities).where(
                    horse_affinities.c.horse_id == parent['id']).values(grade=grade))
            GameService(db, self.now, 'foal-test', 'player').inventory_change(
                'premium_grass', 2, 'test_grant', 'premium-grass')

    def test_conception_freezes_inheritance_and_delivery_uses_it(self):
        with self.store.engine.begin() as db:
            service = HorseService(db, self.now, 'foal-test', 'player', reference='breed-once')
            service.breed_horses('母云', '父风')
        with self.store.engine.connect() as db:
            pregnancy = dict(db.execute(select(horse_pregnancies)).mappings().one())
        frozen = json.loads(pregnancy['foal_snapshot_json'])
        self.assertIn(frozen['name'], {'父云', '母风'})
        for trait in TRAITS:
            self.assertEqual(frozen['trait_ranges'][trait], [100, 240])
        with self.store.engine.begin() as db:
            db.execute(update(horse_pregnancies).where(
                horse_pregnancies.c.id == pregnancy['id']).values(due_at=self.now))
            service = HorseService(db, self.now, 'foal-test', 'player', reference='deliver-once')
            result = service.deliver_foals()
            foal = result['foals'][0]
            self.assertIn(foal['name'], {'父云', '母风'})
        with self.store.engine.connect() as db:
            stored = dict(db.execute(select(horses).where(horses.c.id == foal['id'])).mappings().one())
        for trait in TRAITS:
            self.assertGreaterEqual(stored[trait], 100)
            self.assertLessEqual(stored[trait], 240)
        stored_snapshot = json.loads(stored['birth_traits_json'])
        self.assertEqual(stored_snapshot['name'], stored['name'])
        self.assertEqual(stored_snapshot['traits'], frozen['traits'])
        self.assertEqual(stored_snapshot['p8_affinity_inheritance']['result'],
                         result['foals'][0]['snapshot']['p8_affinity_inheritance']['result'])
        self.assertEqual(stored['father_id'], self.father['id'])
        self.assertEqual(stored['mother_id'], self.mother['id'])

    def test_group_admin_force_delivery_delivers_pending_foal_and_quotes_command(self):
        self.store.admins.add('player')
        with self.store.engine.begin() as db:
            HorseService(db, self.now, 'foal-test', 'player', reference='group-force-breed').breed_horses(
                '母云', '父风')
            pregnancy = db.execute(select(horse_pregnancies)).mappings().one()
            pregnancy_id = pregnancy['id']
            db.execute(update(horse_pregnancies).where(
                horse_pregnancies.c.id == pregnancy_id).values(due_at=self.now + 3600))

        result = self.store.receive(Inbound(room='room', sender='player', name='管理员',
            message_id='group-force-delivery', text='/强制接生'))

        self.assertTrue(result['queued'])
        with self.store.engine.connect() as db:
            pregnancy = db.execute(select(horse_pregnancies).where(
                horse_pregnancies.c.id == pregnancy_id)).mappings().one()
            reply = db.execute(select(outbox).where(
                outbox.c.reply_to_message_id == 'group-force-delivery')).mappings().one()
        self.assertEqual(pregnancy['status'], 'delivered')
        self.assertEqual(reply['kind'], 'group')
        self.assertEqual(reply['reply_to_sender_id'], 'player')
        self.assertIn('幼驹', reply['text'])


if __name__ == '__main__':
    unittest.main()
