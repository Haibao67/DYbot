"""Persist game-chat administrator grants without changing existing admins."""
from alembic import op
from dzmm_bot.persistence.schema import game_admin_members

revision = "0016_game_admin_login"
down_revision = "0015_horse_growth_ranks"
branch_labels = depends_on = None


def upgrade():
    game_admin_members.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a verified backup to remove game administrator grants.")
