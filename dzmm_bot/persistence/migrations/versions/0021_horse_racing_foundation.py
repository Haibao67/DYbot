"""P8-1 to P8-4 structures, with U/UNINITIALIZED legacy state; no random migration."""
import sqlalchemy as sa
from alembic import op

revision = '0021_horse_racing_foundation'
down_revision = '0020_p75_compact_replies'
branch_labels = depends_on = None


def upgrade():
    # Freeze this revision's DDL; do not import mutable application metadata.
    op.create_table('horse_affinities', sa.Column('horse_id', sa.String(200), primary_key=True),
        sa.Column('category', sa.String(20), primary_key=True), sa.Column('key', sa.String(20), primary_key=True),
        sa.Column('grade', sa.String(1), nullable=False, server_default='U'),
        sa.Column('source', sa.String(40), nullable=False), sa.Column('generation_version', sa.String(80)),
        sa.Column('finalized_at', sa.Float()), sa.CheckConstraint("grade IN ('U','S','A','B','C','D')"),
        sa.CheckConstraint("(category='distance' AND key IN ('short','mile','medium','long')) OR "
            "(category='surface' AND key IN ('turf','dirt')) OR "
            "(category='running_style' AND key IN ('front','stalker','mid','closer'))"))
    op.create_table('horse_racing_profiles', sa.Column('horse_id', sa.String(200), primary_key=True),
        sa.Column('skills_state', sa.String(20), nullable=False, server_default='UNINITIALIZED'),
        sa.Column('updated_at', sa.Float(), nullable=False),
        sa.CheckConstraint("skills_state IN ('UNINITIALIZED','FINALIZED')"))
    op.create_table('skill_definitions', sa.Column('skill_id', sa.String(200), primary_key=True),
        sa.Column('code', sa.String(80), nullable=False, unique=True), sa.Column('version', sa.String(80), nullable=False),
        sa.Column('definition_json', sa.Text(), nullable=False), sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.Float(), nullable=False))
    op.create_table('horse_skills', sa.Column('horse_skill_id', sa.String(200), primary_key=True),
        sa.Column('horse_id', sa.String(200), sa.ForeignKey('horses.id'), nullable=False),
        sa.Column('skill_id', sa.String(200), sa.ForeignKey('skill_definitions.skill_id'), nullable=False),
        sa.Column('slot', sa.Integer(), nullable=False), sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(40), nullable=False), sa.Column('source_parent_id', sa.String(200)),
        sa.Column('created_at', sa.Float(), nullable=False), sa.Column('updated_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('horse_id','skill_id'), sa.UniqueConstraint('horse_id','slot'),
        sa.CheckConstraint('slot BETWEEN 1 AND 6 AND level BETWEEN 1 AND 10'),
        sa.CheckConstraint("source IN ('INHERITANCE_FATHER','INHERITANCE_MOTHER','INHERITANCE_MERGE',"
            "'INHERITANCE_RANDOM','RACE_REWARD','LEGACY_INITIALIZER','ADMIN')"))
    op.create_table('horse_skill_inheritance', sa.Column('horse_id', sa.String(200), primary_key=True),
        sa.Column('birth_event_id', sa.String(200), nullable=False), sa.Column('snapshot_json', sa.Text(), nullable=False),
        sa.Column('algorithm_version', sa.String(80), nullable=False), sa.Column('created_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('birth_event_id'))
    op.create_table('horse_race_results', sa.Column('race_id', sa.String(200), primary_key=True),
        sa.Column('input_json', sa.Text(), nullable=False), sa.Column('result_json', sa.Text(), nullable=False),
        sa.Column('simulation_version', sa.String(80), nullable=False), sa.Column('rng_seed', sa.String(200), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False))
    ids = list(op.get_bind().execute(sa.text('SELECT id FROM horses')).scalars())
    affinity = sa.table('horse_affinities', sa.column('horse_id'), sa.column('category'),
        sa.column('key'), sa.column('grade'), sa.column('source'))
    profile = sa.table('horse_racing_profiles', sa.column('horse_id'), sa.column('skills_state'), sa.column('updated_at'))
    keys = {'distance': ('short','mile','medium','long'), 'surface': ('turf','dirt'),
            'running_style': ('front','stalker','mid','closer')}
    for horse_id in ids:
        op.get_bind().execute(affinity.insert(), [dict(horse_id=horse_id, category=category,
            key=key, grade='U', source='LEGACY') for category, values in keys.items() for key in values])
        op.get_bind().execute(profile.insert().values(horse_id=horse_id, skills_state='UNINITIALIZED', updated_at=0))


def downgrade():
    raise RuntimeError('Restore a verified backup to reverse P8 foundation migration.')
