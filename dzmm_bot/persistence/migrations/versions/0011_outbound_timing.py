"""Record the latest outbound attempt milestones without rewriting old messages."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0011_outbound_timing"
down_revision = "0010_group_invites"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "outbound_messages" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("outbound_messages")}
    for name in ("last_claimed_at", "last_send_started_at", "last_result_at"):
        if name not in columns:
            op.add_column("outbound_messages", sa.Column(name, sa.Float(), nullable=True))
    indexes = {index["name"] for index in inspect(bind).get_indexes("outbound_messages")}
    if "ix_outbound_status_created" not in indexes:
        op.create_index("ix_outbound_status_created", "outbound_messages", ["status", "created"])
    if "ix_outbound_last_result" not in indexes:
        op.create_index("ix_outbound_last_result", "outbound_messages", ["last_result_at"])
    if "ix_outbound_status_kind_reply" not in indexes:
        op.create_index("ix_outbound_status_kind_reply", "outbound_messages",
                        ["status", "kind", "reply_to_message_id"])


def downgrade():
    raise RuntimeError("Restore a verified backup to remove outbound timing history.")
