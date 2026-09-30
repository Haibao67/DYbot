"""Save simulation exactly once, without introducing future prize/registration logic."""
from dataclasses import asdict
import hashlib
import json
from sqlalchemy import select, text
from dzmm_bot.persistence.schema import horse_race_results


class RaceService:
    def __init__(self, db, now, engine):
        self.db, self.now, self.engine = db, now, engine

    def get_result(self, race_id):
        row = self.db.execute(select(horse_race_results.c.result_json).where(
            horse_race_results.c.race_id == race_id)).scalar()
        return json.loads(row) if row else None

    def finalize(self, context):
        if context.rng is not None:
            raise ValueError('Persisted races require seed-based RNG; injected RNG is domain-only')
        # PostgreSQL serializes competing workers, including races with no row yet.
        # SQLite callers must hold the existing BEGIN IMMEDIATE write transaction.
        if self.db.dialect.name == 'postgresql':
            lock = int.from_bytes(hashlib.sha256(('race:' + context.race_id).encode()).digest()[:8], 'big', signed=True)
            self.db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
        elif self.db.dialect.name == 'sqlite':
            self.db.execute(text('UPDATE horse_race_results SET race_id = race_id WHERE 1=0'))
        row = self.db.execute(select(horse_race_results).where(
            horse_race_results.c.race_id == context.race_id).with_for_update()).mappings().first()
        input_data = {key: value for key, value in asdict(context).items()
                      if key not in {'rng', 'phase', 'checkpoint_index'}}
        if not input_data.get('rule_metadata'):
            input_data.pop('rule_metadata',None)
        input_data['participants'] = sorted(input_data['participants'], key=lambda horse: horse['horse_id'])
        body = json.dumps(input_data, ensure_ascii=False, sort_keys=True, default=str)
        if row:
            if row['input_json'] != body:
                raise ValueError('Race input snapshot conflict')
            return json.loads(row['result_json'])
        result = self.engine.simulate(context)
        result_body = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        self.db.execute(horse_race_results.insert().values(race_id=context.race_id, input_json=body,
            result_json=result_body, simulation_version=context.simulation_version,
            rng_seed=context.rng_seed, created_at=self.now))
        return json.loads(result_body)
