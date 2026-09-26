"""Preserve existing transport tables and add the global economy."""
import json
from alembic import op
from dzmm_bot.persistence.transport import meta as transport
from dzmm_bot.persistence.schema import M0_TABLES, configs, world
from pathlib import Path
BASE_CONFIG = json.loads((Path(__file__).parent.parent / "config_snapshots" / "v1.json").read_text(encoding="utf-8"))

revision = "0001_foundation"
down_revision = None
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    transport.create_all(db, checkfirst=True)
    for table in M0_TABLES:
        table.create(db)
    op.bulk_insert(configs, [{"version": "v1", "payload_json": json.dumps(BASE_CONFIG), "status": "published", "published_at": 0}])
    op.bulk_insert(world, [{"id": 1, "config_version": "v1", "lottery_pool_balance": 0, "updated_at": 0}])
    # Protect economic facts even against accidental UPDATE/DELETE in application code.
    for table in ("currency_ledger", "inventory_ledger", "game_config_versions", "admin_audit_log", "game_events"):
        if db.dialect.name == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable record'); END")
        elif db.dialect.name == "postgresql":
            op.execute("CREATE OR REPLACE FUNCTION reject_game_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable record'; END; $$")
            op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_game_mutation()")


def downgrade():
    raise RuntimeError("Game ledgers cannot be downgraded destructively; restore a verified backup.")
