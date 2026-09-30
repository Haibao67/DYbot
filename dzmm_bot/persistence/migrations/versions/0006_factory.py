"""Add persistent Ruihe processing factories and jobs."""
from alembic import op
from dzmm_bot.persistence.schema import FACTORY_TABLES

revision = "0006_factory"
down_revision = "0005_market_cents"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in FACTORY_TABLES:
        table.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back factory job history.")
