"""Persist sanitized outbound failure diagnostics."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008_outbound_failure_details"
down_revision = "0007_buffs_factory_advanced"
branch_labels = depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    if "outbound_messages" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("outbound_messages")}
    if "error_detail" not in columns:
        op.add_column("outbound_messages", sa.Column("error_detail", sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError("Restore a verified backup to remove outbound failure diagnostics.")
