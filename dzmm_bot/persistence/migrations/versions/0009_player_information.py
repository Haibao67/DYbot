"""Add update registry, durable broadcast state, player tips and world events."""
from alembic import op
from dzmm_bot.persistence.schema import INFORMATION_TABLES

revision = "0009_player_information"
down_revision = "0008_outbound_failure_details"
branch_labels = depends_on = None


def upgrade():
    for table in INFORMATION_TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a verified backup to remove player information history.")
