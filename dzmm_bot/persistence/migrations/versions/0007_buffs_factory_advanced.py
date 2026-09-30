"""Add shared player buffs and durable advanced factory state."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007_buffs_factory_advanced"
down_revision = "0006_factory"
branch_labels = depends_on = None

ACTIVE_INDEX = "uq_processing_active_line"
STATUS_CHECK = "ck_processing_jobs_status"
STATUS_SQL = "status IN ('processing', 'completed_pending_collect', 'collected', 'failed', 'cancelled')"
STATUS_VALUES = ("processing", "completed_pending_collect", "collected", "failed", "cancelled")


def _has_current_status_check(checks):
    return any(all(value in (check.get("sqltext") or "") for value in STATUS_VALUES)
               for check in checks)


def _columns(table):
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _add_column(table, column):
    if column.name not in _columns(table):
        op.add_column(table, column)


def _rebuild_sqlite_jobs():
    db = op.get_bind()
    op.execute("DROP INDEX IF EXISTS uq_processing_active_line")
    op.execute("""CREATE TABLE processing_jobs_p5 (
        id VARCHAR(200) NOT NULL PRIMARY KEY,
        player_id VARCHAR(200),
        line_no INTEGER NOT NULL,
        recipe_id VARCHAR(40) NOT NULL,
        recipe_version VARCHAR(40) NOT NULL,
        batch_quantity INTEGER NOT NULL,
        started_at FLOAT NOT NULL,
        finish_at FLOAT NOT NULL,
        status VARCHAR(20) NOT NULL,
        input_snapshot_json TEXT NOT NULL,
        output_snapshot_json TEXT NOT NULL,
        failure_roll VARCHAR(80) NOT NULL,
        critical_roll VARCHAR(80) NOT NULL,
        failed BOOLEAN NOT NULL,
        critical BOOLEAN NOT NULL,
        collected_at FLOAT,
        market_window_id BIGINT,
        price_snapshot_json TEXT NOT NULL,
        failure_rate NUMERIC(8, 6),
        critical_rate NUMERIC(8, 6),
        duration_multiplier NUMERIC(12, 6),
        expedited_at FLOAT,
        cancelled_at FLOAT,
        cancel_refund_json TEXT,
        CONSTRAINT ck_processing_jobs_status CHECK (""" + STATUS_SQL + """),
        CHECK (line_no >= 1 AND batch_quantity >= 1)
    )""")
    columns = [row["name"] for row in inspect(db).get_columns("processing_jobs")]
    names = ", ".join(f'"{name}"' for name in columns)
    op.execute(f"INSERT INTO processing_jobs_p5 ({names}) SELECT {names} FROM processing_jobs")
    op.execute("DROP TABLE processing_jobs")
    op.execute("ALTER TABLE processing_jobs_p5 RENAME TO processing_jobs")


def upgrade():
    db = op.get_bind()
    _add_column("processing_factories", sa.Column("rush_used_on", sa.String(10)))
    _add_column("processing_factories", sa.Column("rush_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column("processing_factories", sa.Column("automation_level", sa.Integer(), nullable=False, server_default="0"))

    _add_column("processing_jobs", sa.Column("failure_rate", sa.Numeric(8, 6)))
    _add_column("processing_jobs", sa.Column("critical_rate", sa.Numeric(8, 6)))
    _add_column("processing_jobs", sa.Column("duration_multiplier", sa.Numeric(12, 6)))
    _add_column("processing_jobs", sa.Column("expedited_at", sa.Float()))
    _add_column("processing_jobs", sa.Column("cancelled_at", sa.Float()))
    _add_column("processing_jobs", sa.Column("cancel_refund_json", sa.Text()))

    if db.dialect.name == "sqlite":
        checks = inspect(db).get_check_constraints("processing_jobs")
        if not any("completed_pending_collect" in (check.get("sqltext") or "") for check in checks):
            _rebuild_sqlite_jobs()
    elif db.dialect.name == "postgresql":
        checks = inspect(db).get_check_constraints("processing_jobs")
        if not _has_current_status_check(checks):
            for check in checks:
                sqltext = check.get("sqltext") or ""
                if check.get("name") == STATUS_CHECK or "status" in sqltext.lower():
                    op.drop_constraint(check["name"], "processing_jobs", type_="check")
            op.create_check_constraint(STATUS_CHECK, "processing_jobs", STATUS_SQL)

    indexes = {index["name"] for index in inspect(db).get_indexes("processing_jobs")}
    if ACTIVE_INDEX in indexes:
        op.drop_index(ACTIVE_INDEX, table_name="processing_jobs")
    op.create_index(ACTIVE_INDEX, "processing_jobs", ["player_id", "line_no"], unique=True,
                    sqlite_where=sa.text("status IN ('processing', 'completed_pending_collect')"),
                    postgresql_where=sa.text("status IN ('processing', 'completed_pending_collect')"))

    from dzmm_bot.persistence.schema import buffs
    buffs.create(db, checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back Buff and factory operation history.")
