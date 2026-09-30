"""Persist the inactivity window selected for each red packet."""
import sqlalchemy as sa
from alembic import op

revision = "0027_red_packet_duration"
down_revision = "0026_daily_weather"
branch_labels = depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("red_packets")}
    if "duration_seconds" not in columns:
        op.add_column("red_packets", sa.Column("duration_seconds", sa.Integer(),
                                                 nullable=False, server_default="120"))


def downgrade():
    raise RuntimeError("Restore a reviewed backup; preserve red packet expiry rules and escrow history")
