"""P8-6 through P8-10 persistent lifecycle. No changes to existing assets."""
import sqlalchemy as sa
from alembic import op

revision = '0022_race_lifecycle'
down_revision = '0021_horse_racing_foundation'
branch_labels = depends_on = None


def upgrade():
    op.create_table('race_definitions', sa.Column('race_id', sa.String(200), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False), sa.Column('definition_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False), sa.Column('registration_open_at', sa.Float(), nullable=False),
        sa.Column('registration_close_at', sa.Float(), nullable=False), sa.Column('starts_at', sa.Float(), nullable=False),
        sa.Column('snapshot_json', sa.Text()), sa.Column('settled_at', sa.Float()), sa.Column('created_at', sa.Float(), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','REGISTRATION','LOCKED','RUNNING','FINISHED','CANCELLED')"),
        sa.CheckConstraint('registration_open_at < registration_close_at AND registration_close_at <= starts_at'))
    op.create_table('race_entries', sa.Column('entry_id', sa.String(200), primary_key=True),
        sa.Column('race_id', sa.String(200), sa.ForeignKey('race_definitions.race_id'), nullable=False),
        sa.Column('horse_id', sa.String(200), sa.ForeignKey('horses.id'), nullable=False),
        sa.Column('player_id', sa.String(200), nullable=False), sa.Column('running_style', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False), sa.Column('entry_fee_paid', sa.BigInteger(), nullable=False),
        sa.Column('registered_at', sa.Float(), nullable=False), sa.UniqueConstraint('race_id', 'horse_id'),
        sa.CheckConstraint("status IN ('REGISTERED','LOCKED','FINISHED','CANCELLED')"),
        sa.CheckConstraint("running_style IN ('front','stalker','mid','closer')"), sa.CheckConstraint('entry_fee_paid >= 0'))
    op.create_table('horse_race_locks', sa.Column('horse_id', sa.String(200), sa.ForeignKey('horses.id'), primary_key=True),
        sa.Column('race_id', sa.String(200), sa.ForeignKey('race_definitions.race_id'), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False))
    op.create_table('race_state_log', sa.Column('id', sa.String(200), primary_key=True),
        sa.Column('race_id', sa.String(200), sa.ForeignKey('race_definitions.race_id'), nullable=False),
        sa.Column('from_status', sa.String(20), nullable=False), sa.Column('to_status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False))
    op.create_table('horse_race_performances', sa.Column('race_id', sa.String(200), sa.ForeignKey('race_definitions.race_id'), primary_key=True),
        sa.Column('horse_id', sa.String(200), sa.ForeignKey('horses.id'), primary_key=True),
        sa.Column('player_id', sa.String(200), nullable=False), sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('prize', sa.BigInteger(), nullable=False), sa.Column('season_id', sa.String(80), nullable=False),
        sa.Column('performance_json', sa.Text(), nullable=False), sa.Column('created_at', sa.Float(), nullable=False),
        sa.CheckConstraint('rank > 0 AND prize >= 0'))
    op.create_table('horse_affinity_inheritance', sa.Column('horse_id', sa.String(200), sa.ForeignKey('horses.id'), primary_key=True),
        sa.Column('birth_event_id', sa.String(200), nullable=False, unique=True), sa.Column('snapshot_json', sa.Text(), nullable=False),
        sa.Column('algorithm_version', sa.String(80), nullable=False), sa.Column('created_at', sa.Float(), nullable=False))
    op.create_index('ix_race_due','race_definitions',['status','registration_close_at','starts_at'])
    op.create_index('ix_race_entries_player_status','race_entries',['player_id','status'])
    op.create_index('ix_race_locks_race','horse_race_locks',['race_id'])
    op.create_index('ix_race_performance_horse_time','horse_race_performances',['horse_id','created_at'])


def downgrade():
    raise RuntimeError('Restore a reviewed backup; racing records must not be deleted automatically')
