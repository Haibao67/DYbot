"""Add persistent global auctions, bids, reservations and horse custody."""
from alembic import op
from dzmm_bot.persistence.schema import AUCTION_TABLES

revision = "0017_auctions"
down_revision = "0016_game_admin_login"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in AUCTION_TABLES:
        table.create(bind, checkfirst=True)
        for index in table.indexes:
            index.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a verified backup to remove auction data.")
