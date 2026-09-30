"""Persist animal rarity and completed-batch harvest snapshots."""
import sqlalchemy as sa
from alembic import op

revision = "0019_ranch_animal_rarity"
down_revision = "0018_ranch_grouped_production"
branch_labels = depends_on = None


def upgrade():
    additions = {
        "ranch_animals": [("rarity", sa.String(40), "normal")],
        "ranch_products": [
            ("production_base", sa.Integer(), "1"), ("animal_count", sa.Integer(), "0"),
            ("output_multiplier", sa.Numeric(20, 8), "1"),
            ("rarity_counts_json", sa.Text(), "{}"),
            ("rarity_rule_version", sa.String(40), "ranch-rarity-v1"),
            ("harvest_base_bonus", sa.Integer(), "0"),
        ],
    }
    bind = op.get_bind()
    for table, fields in additions.items():
        existing = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        for name, kind, default in fields:
            if name not in existing:
                op.add_column(table, sa.Column(name, kind, nullable=False, server_default=default))


def downgrade():
    raise RuntimeError("Restore a verified backup to remove animal rarity snapshots.")
