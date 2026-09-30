"""Horse catalog functional/economic tests against temporary SQLite only."""
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select, text
from dzmm_bot.store import Store
from dzmm_bot.core import Inbound
from dzmm_bot.persistence.transport import rooms, outbox
from dzmm_bot.persistence.schema import horses, accounts, currency
from dzmm_bot.application.services import GameService, metrics
from dzmm_bot.application.horse_service import HorseService
from dzmm_bot.domain.horse_catalog import load_catalog, catalog_snapshot, birth_catalog, DISTANCES, STYLES
from dzmm_bot.domain.horse_rules import g0_snapshot, TRAITS


class CatalogRulesTests(unittest.TestCase):
    def test_stable_capacity_starts_at_three_and_increases_one_per_level(self):
        from dzmm_bot.domain.horse_rules import CAPACITIES
        self.assertEqual(CAPACITIES, (3, 4, 5, 6, 7))

    def test_catalog_contains_only_109_chinese_names(self):
        rows=load_catalog()
        self.assertEqual(len(rows),109)
        self.assertEqual(len({r['name'] for r in rows}),109)
        self.assertTrue(all(not any(c.isascii() and c.isalpha() for c in r['name']) for r in rows))

    def test_fixed_seed_is_reproducible(self):
        self.assertEqual(catalog_snapshot('fixed'),catalog_snapshot('fixed'))

    def test_every_template_stat_bounds_and_candidate_choices(self):
        rows=load_catalog()
        for row in rows:
            with patch('dzmm_bot.domain.horse_catalog.load_catalog',return_value=[row]):
                for seed in range(12):
                    snapshot=catalog_snapshot(str(seed)); origin=snapshot['catalog']
                    for trait in TRAITS:
                        self.assertGreaterEqual(snapshot['traits'][trait],row['scores'][trait]*12)
                        self.assertLessEqual(snapshot['traits'][trait],row['scores'][trait]*18)
                    self.assertIn(origin['recommended_distance'],[DISTANCES[x] for x in row['race_options'] if x in DISTANCES])
                    self.assertIn(origin['recommended_style'],[STYLES[x] for x in row['style_options']])
                    self.assertEqual(origin['recommended_surface'],'dirt' if '泥地' in row['race_options'] else 'turf')

    def test_rng_injection_and_inclusive_endpoints(self):
        class BoundaryRng:
            def __init__(self,upper): self.upper=upper
            def choice(self,items): return items[-1] if self.upper else items[0]
            def randint(self,low,high): return high if self.upper else low
        for upper in (False,True):
            snapshot=catalog_snapshot('boundary',rng=BoundaryRng(upper))
            for trait in TRAITS:
                self.assertEqual(snapshot['traits'][trait],snapshot['catalog']['scores'][trait]*(18 if upper else 12))

    def test_invalid_generation_parameters(self):
        for multiplier,variation in [(0,.2),(15,-1),(15,1),(100,.2),('NaN',.2)]:
            with self.assertRaises(ValueError): catalog_snapshot('bad',multiplier,variation)

    def test_uniform_selection_uses_complete_candidate_lists(self):
        class RecordingRng(random.Random):
            def __init__(self): super().__init__(1); self.choices=[]
            def choice(self,seq): self.choices.append(list(seq)); return seq[0]
        rng=RecordingRng(); snapshot=catalog_snapshot('fixed',rng=rng)
        self.assertEqual(len(rng.choices[0]),109)
        self.assertEqual(rng.choices[1],['medium','long'])
        self.assertEqual(rng.choices[2],['stalker','mid'])


class CatalogDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.url='sqlite:///'+str(Path(self.tmp.name)/'catalog.db')
        self.now=1790596800.0; self.number=0
        self.store=Store(self.url,secret='catalog-test',clock=lambda:self.now)
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert().values(id='test-room',kind='group',enabled=1))
        self.send('/注册 测试玩家')
        self.fund(10000)

    def send(self,command,mid=None):
        self.number+=1
        return self.store.receive(Inbound(room='test-room',sender='test-player',name='测试玩家',
            message_id=mid or str(self.number),text=command))

    def fund(self,amount):
        with self.store.engine.begin() as db:
            GameService(db,self.now,'catalog-test','test-player').admin_action('compensate','test',
                amount=amount,reason='test',ticket='fund-'+str(amount))

    def rows(self,table):
        with self.store.engine.connect() as db:
            return [dict(row) for row in db.execute(select(table)).mappings()]

    def balance(self): return self.rows(accounts)[0]['balance']

    def assert_reconciled(self):
        with self.store.engine.connect() as db:
            report=metrics(db)
        self.assertEqual(report['balance_difference'],0)
        self.assertEqual(report['account_mismatches'],[])
        self.assertGreaterEqual(self.balance(),0)

    def test_purchase_debits_1000_and_records_one_ledger(self):
        before=self.balance(); self.send('/买马')
        horse=self.rows(horses)[0]; origin=birth_catalog(horse)
        self.assertEqual(before-self.balance(),100000)
        ledger=[r for r in self.rows(currency) if r['reason']=='horse_buy']
        self.assertEqual(len(ledger),1); self.assertEqual(ledger[0]['amount'],-100000)
        self.assertIn(horse['name'],[r['name'] for r in load_catalog()])
        self.assertEqual(horse['rule_version'],'horse_catalog_v1')
        for trait in TRAITS: self.assertEqual(horse[trait],json.loads(horse['birth_traits_json'])['traits'][trait])
        self.assertEqual(origin['multiplier'],'15'); self.assert_reconciled()

    def test_purchase_reply_references_original_message(self):
        self.send('/买马',mid='purchase-original')
        reply=self.rows(outbox)[-1]
        self.assertEqual(reply['reply_to_message_id'],'purchase-original')
        self.assertIn('已购买',reply['text']); self.assertIn('赛程',reply['text'])

    def test_duplicate_message_does_not_duplicate_purchase(self):
        before=self.balance(); self.send('/买马',mid='same'); self.send('/买马',mid='same')
        self.assertEqual(len(self.rows(horses)),1); self.assertEqual(before-self.balance(),100000)
        self.assert_reconciled()

    def test_insufficient_balance_preserves_assets(self):
        self.fund(999-self.balance()//100)
        before=self.balance(); self.send('/买马')
        self.assertEqual(self.rows(horses),[]); self.assertEqual(self.balance(),before)
        self.assertFalse(any(r['reason']=='horse_buy' for r in self.rows(currency)))
        self.assert_reconciled()

    def test_full_stable_does_not_charge(self):
        for _ in range(3): self.send('/买马')
        before=self.balance(); self.send('/买马')
        self.assertEqual(len(self.rows(horses)),3); self.assertEqual(self.balance(),before)
        self.assert_reconciled()

    def test_duplicate_template_has_unique_chinese_name(self):
        snapshot=catalog_snapshot('fixed-name')
        with patch('dzmm_bot.application.horse_service.catalog_snapshot',return_value=snapshot):
            self.send('/买马'); self.send('/买马')
        names=[r['name'] for r in self.rows(horses)]
        self.assertEqual(names,[snapshot['catalog']['name'],snapshot['catalog']['name']+'·二号'])
        self.assert_reconciled()

    def test_restart_preserves_snapshot_and_recommendation(self):
        self.send('/买马'); horse=self.rows(horses)[0]; origin=birth_catalog(horse)
        restarted=Store(self.url,secret='catalog-test',clock=lambda:self.now)
        self.addCleanup(restarted.engine.dispose)
        with restarted.engine.begin() as db:
            result=HorseService(db,self.now,'catalog-test','test-player').detail(horse['name'])['horse']
            self.assertEqual(result['recommendation'],[origin['recommended_distance'],origin['recommended_style']])
            self.assertEqual(result['birth_traits_json'],horse['birth_traits_json'])
        self.assertEqual(self.rows(horses)[0]['birth_traits_json'],horse['birth_traits_json'])

    def test_rename_keeps_origin(self):
        self.send('/买马'); horse=self.rows(horses)[0]
        self.send('/马匹命名 '+horse['name']+' 中文新名')
        updated=self.rows(horses)[0]
        self.assertEqual(updated['name'],'中文新名')
        self.assertEqual(updated['birth_traits_json'],horse['birth_traits_json'])

    def test_failure_after_debit_rolls_back_all_assets(self):
        before=self.balance()
        with self.assertRaises(RuntimeError):
            with self.store.engine.begin() as db:
                with patch('dzmm_bot.application.horse_racing_service.HorseRacingService.finalize_affinity',side_effect=RuntimeError('injected')):
                    HorseService(db,self.now,'catalog-test','test-player',reference='rollback').buy_horse()
        self.assertEqual(self.balance(),before); self.assertEqual(self.rows(horses),[])
        self.assertFalse(any(r['reason']=='horse_buy' for r in self.rows(currency)))
        self.assert_reconciled()

    def test_concurrent_duplicate_delivery_charges_once(self):
        before=self.balance()
        inbound=Inbound(room='test-room',sender='test-player',message_id='concurrent',text='/买马')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:self.store.receive(inbound),range(8)))
        self.assertEqual(len(self.rows(horses)),1); self.assertEqual(before-self.balance(),100000)
        self.assert_reconciled()

    def test_legacy_horse_snapshot_and_stats_remain_unchanged(self):
        self.send('/买马'); horse=self.rows(horses)[0]; legacy=g0_snapshot('legacy-seed')
        body=json.dumps(legacy,ensure_ascii=False)
        with self.store.engine.begin() as db:
            db.execute(horses.update().where(horses.c.id==horse['id']).values(name='旧马',birth_traits_json=body,
                rule_version='ruihe-horse-v1',**legacy['traits']))
        self.send('/马 旧马'); self.send('/马厩')
        row=self.rows(horses)[0]
        self.assertEqual(row['birth_traits_json'],body); self.assertIsNone(birth_catalog(row))
        for trait in TRAITS: self.assertEqual(row[trait],legacy['traits'][trait])

    def test_database_is_at_head_and_repeat_migration_preserves_data(self):
        self.send('/买马'); before=self.rows(horses); balance=self.balance()
        from dzmm_bot.persistence.migrate import migrate
        migrate(self.store.engine)
        self.assertEqual(self.rows(horses),before); self.assertEqual(self.balance(),balance)
        with self.store.engine.connect() as db:
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            config=Config(); config.set_main_option('script_location','dzmm_bot/persistence/migrations')
            self.assertEqual(db.execute(text('select version_num from alembic_version')).scalar(),ScriptDirectory.from_config(config).get_current_head())
        self.assert_reconciled()

    def test_concurrent_distinct_purchases_cannot_overdraw(self):
        self.fund(1500-self.balance()//100)
        before=self.balance()
        def buy(index):
            return self.store.receive(Inbound(room='test-room',sender='test-player',
                message_id='competing-'+str(index),text='/买马'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(buy,range(2)))
        self.assertEqual(len(self.rows(horses)),1)
        self.assertEqual(self.balance(),before-100000)
        self.assertEqual(len([r for r in self.rows(currency) if r['reason']=='horse_buy']),1)
        self.assert_reconciled()

    def test_concurrent_distinct_purchases_cannot_exceed_capacity(self):
        self.send('/买马'); self.send('/买马')
        before=self.balance()
        def buy(index):
            return self.store.receive(Inbound(room='test-room',sender='test-player',
                message_id='capacity-'+str(index),text='/买马'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(buy,range(2)))
        self.assertEqual(len(self.rows(horses)),3)
        self.assertEqual(self.balance(),before-100000)
        self.assert_reconciled()
