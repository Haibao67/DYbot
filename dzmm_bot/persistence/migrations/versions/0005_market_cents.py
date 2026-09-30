"""Introduce cent-denominated balances and deterministic market ticks."""
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005_market_cents"
down_revision = "0004_ruihe_ranch"
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    columns = {column["name"] for column in inspect(db).get_columns("market_transactions")}
    if "market_window_id" not in columns:
        op.add_column("market_transactions", sa.Column("market_window_id", sa.BigInteger()))
    if "unit_price_cents" not in columns:
        op.add_column("market_transactions", sa.Column("unit_price_cents", sa.BigInteger()))
    indexes = {index["name"] for index in inspect(db).get_indexes("market_transactions")}
    if "ix_market_transactions_window" not in indexes:
        op.create_index("ix_market_transactions_window", "market_transactions",
                        ["player_id", "market_window_id", "kind"])

    # Existing economic values are whole coins. Scaling every side of the existing
    # ledgers in-place preserves balances, reconciliation, and audit history.
    if db.dialect.name == "sqlite":
        for table in ("currency_ledger",):
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS immutable_{table}_{action}")
    else:
        op.execute("DROP TRIGGER IF EXISTS immutable_currency_ledger ON currency_ledger")
    for table, column in (("game_accounts", "balance"), ("currency_ledger", "amount"),
                          ("world_state", "lottery_pool_balance"),
                          ("market_transactions", "gross_amount"),
                          ("market_transactions", "tax_amount"),
                          ("market_transactions", "net_amount")):
        db.execute(sa.text(f"UPDATE {table} SET {column} = {column} * 100"))
    if db.dialect.name == "sqlite":
        op.execute("CREATE TRIGGER immutable_currency_ledger_update BEFORE UPDATE ON currency_ledger BEGIN SELECT RAISE(ABORT, 'immutable record'); END")
        op.execute("CREATE TRIGGER immutable_currency_ledger_delete BEFORE DELETE ON currency_ledger BEGIN SELECT RAISE(ABORT, 'immutable record'); END")
    else:
        op.execute("CREATE TRIGGER immutable_currency_ledger BEFORE UPDATE OR DELETE ON currency_ledger FOR EACH ROW EXECUTE FUNCTION reject_game_mutation()")

    # Publish a forward-only config snapshot with the P3 milk base price. Existing
    # snapshots and transaction config references remain untouched.
    version = "ruihe-market-v1"
    exists = db.execute(sa.text("SELECT 1 FROM game_config_versions WHERE version = :v"), {"v": version}).first()
    if not exists:
        payload = json.loads((Path(__file__).parent.parent / "config_snapshots" / f"{version}.json").read_text(encoding="utf-8"))
        db.execute(sa.text("INSERT INTO game_config_versions(version, payload_json, status, published_at) VALUES (:v, :p, 'published', 0)"),
                   {"v": version, "p": json.dumps(payload, ensure_ascii=False)})
    db.execute(sa.text("UPDATE world_state SET config_version = :v WHERE id = 1"), {"v": version})


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back cent-denominated economic data.")
