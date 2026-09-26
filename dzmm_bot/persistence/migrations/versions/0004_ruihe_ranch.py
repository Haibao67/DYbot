"""Add P2 animal care state and publish the Ruihe ranch rules."""
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004_ruihe_ranch"
down_revision = "0003_reply_context"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("ranch_animals")}
    additions = (
        ("affection", sa.Integer(), "0"),
        ("feed_streak", sa.Integer(), "0"),
        ("premium_feed_active", sa.Boolean(), sa.false()),
        ("cycle_progress", sa.Float(), "0"),
        ("last_feed_reward_at", sa.Float(), "0"),
    )
    for name, kind, default in additions:
        if name not in columns:
            op.add_column("ranch_animals", sa.Column(name, kind, nullable=False, server_default=default))

    versions = {row[0] for row in bind.execute(sa.text("SELECT version FROM game_config_versions"))}
    if "ruihe-ranch-v1" not in versions:
        payload = json.loads((Path(__file__).parent.parent / "config_snapshots" / "ruihe-ranch-v1.json").read_text(encoding="utf-8"))
        bind.execute(sa.text("INSERT INTO game_config_versions(version, payload_json, status, published_at) VALUES (:v, :p, 'published', 0)"),
                     {"v": "ruihe-ranch-v1", "p": json.dumps(payload, ensure_ascii=False)})


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back Ruihe ranch data.")
