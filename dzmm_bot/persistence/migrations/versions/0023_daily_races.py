"""R2 daily race uniqueness and durable event/reward records; preserve existing races."""
import sqlalchemy as sa
from alembic import op

revision='0023_daily_races'
down_revision='0022_race_lifecycle'
branch_labels=depends_on=None


def upgrade():
    with op.batch_alter_table('race_definitions',table_args=(
        sa.CheckConstraint("status IN ('DRAFT','REGISTRATION','LOCKED','RUNNING','FINISHED','CANCELLED')",name='ck_r2_race_status'),
        sa.CheckConstraint('registration_open_at < registration_close_at AND registration_close_at <= starts_at',name='ck_r2_race_times'),
    )) as batch:
        batch.add_column(sa.Column('template_id',sa.String(80)))
        batch.add_column(sa.Column('race_date',sa.String(10)))
        batch.create_unique_constraint('uq_race_template_date',['template_id','race_date'])
    op.create_table('race_rewards',
        sa.Column('race_id',sa.String(200),sa.ForeignKey('race_definitions.race_id'),primary_key=True),
        sa.Column('horse_id',sa.String(200),primary_key=True),sa.Column('reward_type',sa.String(40),primary_key=True),
        sa.Column('player_id',sa.String(200),nullable=False),sa.Column('amount',sa.BigInteger(),nullable=False),
        sa.Column('created_at',sa.Float(),nullable=False),sa.CheckConstraint('amount >= 0'))
    op.create_table('race_events',
        sa.Column('race_id',sa.String(200),sa.ForeignKey('horse_race_results.race_id'),primary_key=True),
        sa.Column('event_index',sa.Integer(),primary_key=True),sa.Column('event_json',sa.Text(),nullable=False),
        sa.Column('created_at',sa.Float(),nullable=False))


def downgrade():
    raise RuntimeError('Restore a reviewed backup; do not delete daily race records')
