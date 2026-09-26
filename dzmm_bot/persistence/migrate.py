from pathlib import Path
from alembic import command
from alembic.config import Config


def migrate(engine):
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as db:
        if engine.dialect.name == "sqlite":
            db.exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            db.exec_driver_sql("SELECT pg_advisory_xact_lock(86420925)")
        cfg.attributes["connection"] = db
        command.upgrade(cfg, "head")
