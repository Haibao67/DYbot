"""Persist lifetime feeding and hourly charges without discarding training history."""
import sqlalchemy as sa
from alembic import op

revision = '0024_horse_feed_budget'
down_revision = '0023_daily_races'
branch_labels = depends_on = None


def upgrade():
    # Keep upgrades safe when the legacy metadata bootstrap already includes
    # these columns (for example an unversioned database created by old code).
    bind = op.get_bind()
    existing = {column['name'] for column in sa.inspect(bind).get_columns('horses')}
    for column in (
        sa.Column('feed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('feed_charges', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('feed_recovered_at', sa.Float()),
    ):
        if column.name not in existing:
            op.add_column('horses', column)
    op.execute(sa.text('UPDATE horses SET feed_count = (SELECT COUNT(*) FROM horse_training_events e WHERE e.horse_id = horses.id)'))


def downgrade():
    raise RuntimeError('Restore a reviewed backup; feeding history must not be reset')
