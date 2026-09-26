"""Ranch production and market, with versioned balance configuration."""
import json
from alembic import op
from dzmm_bot.persistence.schema import M1_TABLES, configs, world
from pathlib import Path
RANCH_CONFIG = json.loads((Path(__file__).parent.parent / "config_snapshots" / "m1-v1.json").read_text(encoding="utf-8"))

revision = "0002_ranch"
down_revision = "0001_foundation"
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    for table in M1_TABLES:
        table.create(db)
    op.bulk_insert(configs, [{"version": "m1-v1", "payload_json": json.dumps(RANCH_CONFIG), "status": "published", "published_at": 0}])
    op.execute(world.update().where(world.c.id == 1).values(config_version="m1-v1"))


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back ranch data.")
