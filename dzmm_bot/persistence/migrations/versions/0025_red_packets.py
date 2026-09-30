"""Audited red packet escrow and unique claims; preserve player assets."""
import sqlalchemy as sa
from alembic import op

revision='0025_red_packets'
down_revision='0024_horse_feed_budget'
branch_labels=depends_on=None


def upgrade():
    op.create_table('red_packets',
        sa.Column('id',sa.String(200),primary_key=True),sa.Column('room_id',sa.String(200)),
        sa.Column('sender_id',sa.String(200)),sa.Column('total_amount',sa.BigInteger(),nullable=False),
        sa.Column('quantity',sa.Integer(),nullable=False),sa.Column('remaining_amount',sa.BigInteger(),nullable=False),
        sa.Column('remaining_count',sa.Integer(),nullable=False),sa.Column('status',sa.String(12),nullable=False),
        sa.Column('created_at',sa.Float(),nullable=False),sa.Column('expires_at',sa.Float(),nullable=False),
        sa.CheckConstraint('total_amount > 0 AND quantity BETWEEN 1 AND 100'),
        sa.CheckConstraint('remaining_amount >= 0 AND remaining_amount <= total_amount AND remaining_count >= 0 AND remaining_count <= quantity'),
        sa.CheckConstraint("status IN ('active','completed','expired')"))
    op.create_index('uq_red_packet_room_active','red_packets',['room_id'],unique=True,
        sqlite_where=sa.text("status='active'"),postgresql_where=sa.text("status='active'"))
    op.create_table('red_packet_claims',sa.Column('packet_id',sa.String(200),primary_key=True),
        sa.Column('player_id',sa.String(200),primary_key=True),sa.Column('amount',sa.BigInteger(),nullable=False),
        sa.Column('claimed_at',sa.Float(),nullable=False),sa.CheckConstraint('amount > 0'))


def downgrade():
    raise RuntimeError('Restore a reviewed backup; do not discard red packet escrow')
