"""Deterministically resolve legacy pending G0 growth ranks."""
import json
import sqlalchemy as sa
from alembic import op
from dzmm_bot.domain.horse_rules import TRAITS, growth_for_seed

revision = "0015_horse_growth_ranks"
down_revision = "0014_farm"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    horses = sa.table("horses", sa.column("id", sa.String), sa.column("seed", sa.String),
        sa.column("birth_traits_json", sa.Text),
        *[sa.column(f"growth_{trait}", sa.String) for trait in TRAITS])
    for row in bind.execute(sa.select(horses)).mappings():
        changes = {f"growth_{trait}": growth_for_seed(row["seed"], trait)
                   for trait in TRAITS if row[f"growth_{trait}"] == "U"}
        if changes:
            snapshot = json.loads(row["birth_traits_json"])
            snapshot.setdefault("growth", {}).update({trait: changes[f"growth_{trait}"]
                for trait in TRAITS if f"growth_{trait}" in changes})
            changes["birth_traits_json"] = json.dumps(snapshot, ensure_ascii=False)
            bind.execute(horses.update().where(horses.c.id == row["id"]).values(**changes))


def downgrade():
    raise RuntimeError("Restore a verified backup to reverse horse growth assignments.")
