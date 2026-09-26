from sqlalchemy import (MetaData, Table, Column, String, Integer, BigInteger, Float, false,
                        Text, Boolean, UniqueConstraint, CheckConstraint, Index)

meta = MetaData()


def table(name, *columns):
    return Table(name, meta, *columns)


def ident(name="id", primary=False):
    return Column(name, String(200), primary_key=primary)


accounts = table("game_accounts", ident("player_id", True),
    Column("balance", BigInteger, nullable=False), Column("joined_at", Float, nullable=False),
    Column("relief_claimed_on", String(10)), Column("status", String(20), nullable=False),
    Column("version", Integer, nullable=False), CheckConstraint("balance >= 0"))
currency = table("currency_ledger", ident("id", True), ident("player_id"),
    Column("amount", BigInteger, nullable=False), Column("reason", String(80), nullable=False),
    Column("reference_type", String(80), nullable=False), ident("reference_id"), ident("source_room_id"),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("player_id", "reference_type", "reference_id"))
# SQL NULLs do not participate in ordinary uniqueness; explicitly protect system entries.
Index("uq_system_currency_reference", currency.c.reference_type, currency.c.reference_id,
      unique=True, sqlite_where=currency.c.player_id.is_(None), postgresql_where=currency.c.player_id.is_(None))
stocks = table("inventory_stacks", ident("player_id", True), Column("item_code", String(40), primary_key=True),
    Column("quantity", BigInteger, nullable=False), Column("version", Integer, nullable=False),
    CheckConstraint("quantity >= 0"))
inventory = table("inventory_ledger", ident("id", True), ident("player_id"),
    Column("item_code", String(40), nullable=False), Column("delta", BigInteger, nullable=False),
    Column("reference_type", String(80), nullable=False), ident("reference_id"),
    Column("config_version", String(40), nullable=False), Column("created_at", Float, nullable=False),
    UniqueConstraint("player_id", "item_code", "reference_type", "reference_id"))
world = table("world_state", Column("id", Integer, primary_key=True),
    Column("config_version", String(40), nullable=False), Column("lottery_pool_balance", BigInteger, nullable=False),
    Column("updated_at", Float, nullable=False), CheckConstraint("id = 1"), CheckConstraint("lottery_pool_balance >= 0"))
configs = table("game_config_versions", Column("version", String(40), primary_key=True),
    Column("payload_json", Text, nullable=False), Column("status", String(20), nullable=False),
    Column("published_at", Float, nullable=False))
audit = table("admin_audit_log", ident("id", True), Column("actor", String(200), nullable=False),
    Column("action", String(80), nullable=False), Column("target_type", String(80), nullable=False),
    ident("target_id"), Column("payload_json", Text, nullable=False), Column("created_at", Float, nullable=False))
events = table("game_events", ident("id", True), ident("player_id"), Column("kind", String(40), nullable=False),
    Column("config_version", String(40), nullable=False), Column("payload_json", Text, nullable=False),
    Column("created_at", Float, nullable=False))
ranches = table("ranches", ident("player_id", True), Column("level", Integer, nullable=False),
    Column("used_capacity", Integer, nullable=False), Column("config_version", String(40), nullable=False),
    Column("version", Integer, nullable=False), Column("created_at", Float, nullable=False),
    CheckConstraint("level >= 1 AND level <= 9"), CheckConstraint("used_capacity >= 0"))
animals = table("ranch_animals", ident("id", True), ident("player_id"),
    Column("display_id", String(12), nullable=False, unique=True), Column("animal_type", String(40), nullable=False),
    Column("space", Integer, nullable=False), Column("feed_cost", Integer, nullable=False),
    Column("base_interval_hours", Float, nullable=False), Column("interval_level", Integer, nullable=False),
    Column("production_until", Float, nullable=False), Column("next_production_at", Float, nullable=False),
    Column("last_settled_at", Float, nullable=False), Column("status", String(20), nullable=False),
    Column("rule_version", String(40), nullable=False), Column("nonce", String(64), nullable=False),
    Column("seed", String(64), nullable=False), Column("seed_commitment", String(64), nullable=False),
    Column("sequence", Integer, nullable=False), Column("affection", Integer, nullable=False, server_default="0"),
    Column("feed_streak", Integer, nullable=False, server_default="0"),
    Column("premium_feed_active", Boolean, nullable=False, server_default=false()),
    Column("cycle_progress", Float, nullable=False, server_default="0"),
    Column("last_feed_reward_at", Float, nullable=False, server_default="0"))
products = table("ranch_products", ident("id", True), ident("player_id"),
    Column("product_type", String(40), nullable=False), Column("quantity", Integer, nullable=False),
    ident("source_animal_id"), Column("produced_at", Float, nullable=False), Column("collected_at", Float),
    Column("sequence", Integer, nullable=False), Column("config_version", String(40), nullable=False),
    UniqueConstraint("source_animal_id", "produced_at"), UniqueConstraint("source_animal_id", "sequence"))
transactions = table("market_transactions", ident("id", True), ident("player_id"),
    Column("kind", String(40), nullable=False), Column("gross_amount", BigInteger, nullable=False),
    Column("tax_amount", BigInteger, nullable=False), Column("net_amount", BigInteger, nullable=False),
    Column("item_code", String(40), nullable=False), Column("quantity", Integer, nullable=False),
    ident("source_room_id"), Column("config_version", String(40), nullable=False),
    Column("created_at", Float, nullable=False))

M0_TABLES = [accounts, currency, stocks, inventory, world, configs, audit, events]
M1_TABLES = [ranches, animals, products, transactions]
