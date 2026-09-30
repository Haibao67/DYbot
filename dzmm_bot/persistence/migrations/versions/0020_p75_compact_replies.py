"""P7.5 compact replies and premium-feed probability snapshot."""
import sqlalchemy as sa
from alembic import op

revision = "0020_p75_compact_replies"
down_revision = "0019_ranch_animal_rarity"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    if "player_reply_pages" not in sa.inspect(bind).get_table_names():
        op.create_table("player_reply_pages", sa.Column("room_id", sa.String(200), primary_key=True),
            sa.Column("player_id", sa.String(200), primary_key=True),
            sa.Column("pages_json", sa.Text(), nullable=False), sa.Column("created_at", sa.Float(), nullable=False))
    if "premium_probability" not in {c["name"] for c in sa.inspect(bind).get_columns("ranch_products")}:
        op.add_column("ranch_products", sa.Column("premium_probability", sa.Numeric(8, 4),
                                               nullable=False, server_default="0"))


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back P7.5.")
