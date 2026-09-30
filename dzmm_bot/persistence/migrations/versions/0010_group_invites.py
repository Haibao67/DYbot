"""Keep private-chat group invitations until an administrator approves them."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from dzmm_bot.persistence.schema import pending_group_invites

revision = "0010_group_invites"
down_revision = "0009_player_information"
branch_labels = depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    if "outbound_messages" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("outbound_messages")}
        if "reply_to_content_json" not in columns:
            op.add_column("outbound_messages", sa.Column("reply_to_content_json", sa.Text(), nullable=True))
    pending_group_invites.create(op.get_bind(), checkfirst=True)
    for index in pending_group_invites.indexes:
        index.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a verified backup to remove invitation history.")
