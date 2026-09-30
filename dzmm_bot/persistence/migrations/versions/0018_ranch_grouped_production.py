"""Mark ranch animal state converted to shared species production."""
import sqlalchemy as sa
from alembic import op

revision = "0018_ranch_grouped_production"
down_revision = "0017_auctions"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("ranch_animals")}
    if "grouped_production" not in columns:
        op.add_column("ranch_animals", sa.Column("grouped_production", sa.Boolean(),
                                               nullable=False, server_default=sa.false()))


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back grouped ranch production.")
