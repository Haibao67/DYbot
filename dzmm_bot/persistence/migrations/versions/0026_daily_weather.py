"""Persist global daily weather and production adjustment audit fields."""
import sqlalchemy as sa
from alembic import op

revision = "0026_daily_weather"
down_revision = "0025_red_packets"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "daily_weather" not in tables:
        op.create_table("daily_weather",
            sa.Column("game_date", sa.String(10), primary_key=True),
            sa.Column("weather_code", sa.String(24), nullable=False),
            sa.Column("weather_rule_version", sa.String(40), nullable=False),
            sa.Column("probability_config_version", sa.String(40), nullable=False),
            sa.Column("seed", sa.String(64), nullable=False),
            sa.Column("generated_at", sa.Float(), nullable=False),
            sa.Column("effective_from", sa.Float(), nullable=False),
            sa.Column("effective_until", sa.Float(), nullable=False),
            sa.Column("definition_snapshot_json", sa.Text(), nullable=False),
            sa.Column("broadcast_enqueued_at", sa.Float()))
    indexes = {index["name"] for index in inspector.get_indexes("daily_weather")}
    if "ix_daily_weather_effective" not in indexes:
        op.create_index("ix_daily_weather_effective", "daily_weather", ["effective_from", "effective_until"])
    for table_name, definitions in {
        "ranch_products": [
            sa.Column("weather_code", sa.String(24)),
            sa.Column("weather_adjustment", sa.Integer()),
            sa.Column("weather_rule_version", sa.String(40)),
        ],
        "farm_plots": [
            sa.Column("harvest_weather_code", sa.String(24)),
            sa.Column("harvest_weather_adjustment", sa.Integer()),
            sa.Column("harvest_weather_rule_version", sa.String(40)),
        ],
    }.items():
        existing = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        for column in definitions:
            if column.name not in existing:
                op.add_column(table_name, column)


def downgrade():
    raise RuntimeError("Restore a reviewed backup; preserve weather and production history")
