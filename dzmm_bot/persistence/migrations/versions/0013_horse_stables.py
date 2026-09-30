"""Add horse breeding records without changing existing player assets."""
from alembic import op
from dzmm_bot.persistence.schema import HORSE_TABLES

revision = "0013_horse_stables"
down_revision = "0012_outbound_scheduler"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in HORSE_TABLES:
        table.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a verified backup to remove horse breeding history.")
