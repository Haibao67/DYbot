"""Add independent farm plots and horse-feed purchase history."""
from alembic import op
from dzmm_bot.persistence.schema import FARM_TABLES

revision = "0014_farm"
down_revision = "0013_horse_stables"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in FARM_TABLES:
        table.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a verified backup to remove farm history.")
