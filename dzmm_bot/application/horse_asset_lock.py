"""Shared horse mutation boundary; all callers use the same horse row lock."""
from sqlalchemy import select
from dzmm_bot.persistence.schema import horses, horse_race_locks, horse_auction_custody
from dzmm_bot.domain.economy import GameError


def assert_horse_mutable(db, horse_id):
    if db.dialect.name == 'sqlite':
        db.execute(horses.update().where(horses.c.id == horse_id).values(version=horses.c.version))
    db.execute(select(horses.c.id).where(horses.c.id == horse_id).with_for_update()).one()
    if db.execute(select(horse_race_locks.c.horse_id).where(horse_race_locks.c.horse_id == horse_id)).first():
        raise GameError('race_locked')
    if db.execute(select(horse_auction_custody.c.horse_id).where(horse_auction_custody.c.horse_id == horse_id)).first():
        raise GameError('horse_missing')
