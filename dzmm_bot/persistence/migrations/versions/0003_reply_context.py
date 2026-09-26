"""Persist the source player message on outbound command replies."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_reply_context"
down_revision = "0002_ranch"
branch_labels = depends_on = None


def upgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("outbound_messages")}
    if "reply_to_message_id" not in columns:
        op.add_column("outbound_messages", sa.Column("reply_to_message_id", sa.String(200), nullable=True))
    if "reply_to_sender_id" not in columns:
        op.add_column("outbound_messages", sa.Column("reply_to_sender_id", sa.String(200), nullable=True))
    if "reply_to_text" not in columns:
        op.add_column("outbound_messages", sa.Column("reply_to_text", sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back reply context.")
