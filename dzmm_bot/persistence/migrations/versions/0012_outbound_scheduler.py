"""Support bounded room-head scheduling without changing existing messages."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0012_outbound_scheduler"
down_revision = "0011_outbound_timing"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    if "group_chats" in inspect(bind).get_table_names():
        columns = {column["name"] for column in inspect(bind).get_columns("group_chats")}
        if "last_dispatched_at" not in columns:
            op.add_column("group_chats", sa.Column("last_dispatched_at", sa.Float(), nullable=True))
    if "outbound_messages" in inspect(bind).get_table_names():
        indexes = {index["name"] for index in inspect(bind).get_indexes("outbound_messages")}
        if "ix_outbound_room_status_created_id" not in indexes:
            op.create_index("ix_outbound_room_status_created_id", "outbound_messages",
                            ["room", "status", "created", "id"])


def downgrade():
    raise RuntimeError("Restore a verified backup to remove outbound scheduler history.")
