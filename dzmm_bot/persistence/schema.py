from sqlalchemy import (MetaData, Table, Column, String, Integer, BigInteger, Float, Numeric, false, ForeignKey,
                        Text, Boolean, UniqueConstraint, CheckConstraint, Index)

meta = MetaData()


def table(name, *columns):
    return Table(name, meta, *columns)


def ident(name="id", primary=False):
    return Column(name, String(200), primary_key=primary)


horse_affinities = table('horse_affinities', ident('horse_id', True),
    Column('category', String(20), primary_key=True), Column('key', String(20), primary_key=True),
    Column('grade', String(1), nullable=False, server_default='U'),
    Column('source', String(40), nullable=False), Column('generation_version', String(80)),
    Column('finalized_at', Float),
    CheckConstraint("grade IN ('U','S','A','B','C','D')"),
    CheckConstraint("(category='distance' AND key IN ('short','mile','medium','long')) OR "
        "(category='surface' AND key IN ('turf','dirt')) OR "
        "(category='running_style' AND key IN ('front','stalker','mid','closer'))"))
horse_racing_profiles = table('horse_racing_profiles', ident('horse_id', True),
    Column('skills_state', String(20), nullable=False, server_default='UNINITIALIZED'),
    Column('updated_at', Float, nullable=False),
    CheckConstraint("skills_state IN ('UNINITIALIZED','FINALIZED')"))
skill_definitions = table('skill_definitions', ident('skill_id', True),
    Column('code', String(80), nullable=False, unique=True), Column('version', String(80), nullable=False),
    Column('definition_json', Text, nullable=False), Column('created_at', Float, nullable=False),
    Column('updated_at', Float, nullable=False))
horse_skills = table('horse_skills', ident('horse_skill_id', True),
    Column('horse_id', String(200), ForeignKey('horses.id'), nullable=False),
    Column('skill_id', String(200), ForeignKey('skill_definitions.skill_id'), nullable=False),
    Column('slot', Integer, nullable=False), Column('level', Integer, nullable=False),
    Column('source', String(40), nullable=False), ident('source_parent_id'),
    Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False),
    UniqueConstraint('horse_id', 'skill_id'), UniqueConstraint('horse_id', 'slot'),
    CheckConstraint('slot BETWEEN 1 AND 6 AND level BETWEEN 1 AND 10'),
    CheckConstraint("source IN ('INHERITANCE_FATHER','INHERITANCE_MOTHER','INHERITANCE_MERGE',"
        "'INHERITANCE_RANDOM','RACE_REWARD','LEGACY_INITIALIZER','ADMIN')"))
horse_skill_inheritance = table('horse_skill_inheritance', ident('horse_id', True),
    Column('birth_event_id', String(200), nullable=False), Column('snapshot_json', Text, nullable=False),
    Column('algorithm_version', String(80), nullable=False), Column('created_at', Float, nullable=False),
    UniqueConstraint('birth_event_id'))
horse_race_results = table('horse_race_results', ident('race_id', True),
    Column('input_json', Text, nullable=False), Column('result_json', Text, nullable=False),
    Column('simulation_version', String(80), nullable=False), Column('rng_seed', String(200), nullable=False),
    Column('created_at', Float, nullable=False))

race_definitions = table('race_definitions', ident('race_id', True),
    Column('template_id', String(80)), Column('race_date', String(10)),
    Column('name', String(100), nullable=False), Column('definition_json', Text, nullable=False),
    Column('status', String(20), nullable=False), Column('registration_open_at', Float, nullable=False),
    Column('registration_close_at', Float, nullable=False), Column('starts_at', Float, nullable=False),
    Column('snapshot_json', Text), Column('settled_at', Float), Column('created_at', Float, nullable=False),
    UniqueConstraint('template_id','race_date',name='uq_race_template_date'),
    CheckConstraint("status IN ('DRAFT','REGISTRATION','LOCKED','RUNNING','FINISHED','CANCELLED')"),
    CheckConstraint('registration_open_at < registration_close_at AND registration_close_at <= starts_at'))
race_entries = table('race_entries', ident('entry_id', True),
    Column('race_id', String(200), ForeignKey('race_definitions.race_id'), nullable=False),
    Column('horse_id', String(200), ForeignKey('horses.id'), nullable=False),
    Column('player_id', String(200), nullable=False), Column('running_style', String(20), nullable=False),
    Column('status', String(20), nullable=False), Column('entry_fee_paid', BigInteger, nullable=False),
    Column('registered_at', Float, nullable=False), UniqueConstraint('race_id', 'horse_id'),
    CheckConstraint("status IN ('REGISTERED','LOCKED','FINISHED','CANCELLED')"),
    CheckConstraint("running_style IN ('front','stalker','mid','closer')"),
    CheckConstraint('entry_fee_paid >= 0'))
horse_race_locks = table('horse_race_locks',
    Column('horse_id', String(200), ForeignKey('horses.id'), primary_key=True),
    Column('race_id', String(200), ForeignKey('race_definitions.race_id'), nullable=False),
    Column('created_at', Float, nullable=False))
race_rewards = table('race_rewards',
    Column('race_id', String(200), ForeignKey('race_definitions.race_id'), primary_key=True),
    Column('horse_id', String(200), primary_key=True), Column('reward_type', String(40), primary_key=True),
    Column('player_id', String(200), nullable=False), Column('amount', BigInteger, nullable=False),
    Column('created_at', Float, nullable=False), CheckConstraint('amount >= 0'))
race_events = table('race_events',
    Column('race_id', String(200), ForeignKey('horse_race_results.race_id'), primary_key=True),
    Column('event_index', Integer, primary_key=True), Column('event_json', Text, nullable=False),
    Column('created_at', Float, nullable=False))
race_state_log = table('race_state_log', ident('id', True),
    Column('race_id', String(200), ForeignKey('race_definitions.race_id'), nullable=False),
    Column('from_status', String(20), nullable=False), Column('to_status', String(20), nullable=False),
    Column('created_at', Float, nullable=False))
horse_race_performances = table('horse_race_performances',
    Column('race_id', String(200), ForeignKey('race_definitions.race_id'), primary_key=True),
    Column('horse_id', String(200), ForeignKey('horses.id'), primary_key=True),
    Column('player_id', String(200), nullable=False), Column('rank', Integer, nullable=False),
    Column('prize', BigInteger, nullable=False), Column('season_id', String(80), nullable=False),
    Column('performance_json', Text, nullable=False), Column('created_at', Float, nullable=False),
    CheckConstraint('rank > 0 AND prize >= 0'))
horse_affinity_inheritance = table('horse_affinity_inheritance',
    Column('horse_id', String(200), ForeignKey('horses.id'), primary_key=True),
    Column('birth_event_id', String(200), nullable=False, unique=True),
    Column('snapshot_json', Text, nullable=False), Column('algorithm_version', String(80), nullable=False),
    Column('created_at', Float, nullable=False))
Index('ix_race_due', race_definitions.c.status, race_definitions.c.registration_close_at, race_definitions.c.starts_at)
Index('ix_race_entries_player_status', race_entries.c.player_id, race_entries.c.status)
Index('ix_race_locks_race', horse_race_locks.c.race_id)
Index('ix_race_performance_horse_time', horse_race_performances.c.horse_id, horse_race_performances.c.created_at)


accounts = table("game_accounts", ident("player_id", True),
    Column("balance", BigInteger, nullable=False), Column("joined_at", Float, nullable=False),
    Column("relief_claimed_on", String(10)), Column("status", String(20), nullable=False),
    Column("version", Integer, nullable=False), CheckConstraint("balance >= 0"))
reply_previews = table("player_reply_pages", ident("room_id", True), ident("player_id", True),
    Column("pages_json", Text, nullable=False), Column("created_at", Float, nullable=False))
red_packets = table('red_packets', ident('id', True), ident('room_id'), ident('sender_id'),
    Column('total_amount', BigInteger, nullable=False), Column('quantity', Integer, nullable=False),
    Column('remaining_amount', BigInteger, nullable=False), Column('remaining_count', Integer, nullable=False),
    Column('status', String(12), nullable=False), Column('created_at', Float, nullable=False),
    Column('expires_at', Float, nullable=False), Column('duration_seconds', Integer, nullable=False, default=120),
    CheckConstraint('total_amount > 0 AND quantity BETWEEN 1 AND 100'),
    CheckConstraint('remaining_amount >= 0 AND remaining_amount <= total_amount AND remaining_count >= 0 AND remaining_count <= quantity'),
    CheckConstraint("status IN ('active','completed','expired')"))
Index('uq_red_packet_room_active', red_packets.c.room_id, unique=True,
    sqlite_where=red_packets.c.status=='active', postgresql_where=red_packets.c.status=='active')
red_packet_claims = table('red_packet_claims', ident('packet_id', True), ident('player_id', True),
    Column('amount', BigInteger, nullable=False), Column('claimed_at', Float, nullable=False),
    CheckConstraint('amount > 0'))
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
game_admin_members = table("game_admin_members", ident("player_id", True),
    Column("granted_at", Float, nullable=False), Column("grant_source", String(40), nullable=False))
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
    Column("grouped_production", Boolean, nullable=False, server_default=false()),
    Column("rarity", String(40), nullable=False, server_default="normal"),
    Column("last_feed_reward_at", Float, nullable=False, server_default="0"))
products = table("ranch_products", ident("id", True), ident("player_id"),
    Column("product_type", String(40), nullable=False), Column("quantity", Integer, nullable=False),
    ident("source_animal_id"), Column("produced_at", Float, nullable=False), Column("collected_at", Float),
    Column("sequence", Integer, nullable=False), Column("config_version", String(40), nullable=False),
    Column("production_base", Integer, nullable=False, server_default="1"),
    Column("animal_count", Integer, nullable=False, server_default="0"),
    Column("output_multiplier", Numeric(20, 8), nullable=False, server_default="1"),
    Column("rarity_counts_json", Text, nullable=False, server_default="{}"),
    Column("rarity_rule_version", String(40), nullable=False, server_default="ranch-rarity-v1"),
    Column("harvest_base_bonus", Integer, nullable=False, server_default="0"),
    Column("premium_probability", Numeric(8, 4), nullable=False, server_default="0"),
    Column("weather_code", String(24)), Column("weather_adjustment", Integer),
    Column("weather_rule_version", String(40)),
    UniqueConstraint("source_animal_id", "produced_at"), UniqueConstraint("source_animal_id", "sequence"))
transactions = table("market_transactions", ident("id", True), ident("player_id"),
    Column("kind", String(40), nullable=False), Column("gross_amount", BigInteger, nullable=False),
    Column("tax_amount", BigInteger, nullable=False), Column("net_amount", BigInteger, nullable=False),
    Column("item_code", String(40), nullable=False), Column("quantity", Integer, nullable=False),
    ident("source_room_id"), Column("config_version", String(40), nullable=False),
    Column("created_at", Float, nullable=False), Column("market_window_id", BigInteger),
    Column("unit_price_cents", BigInteger))
Index("ix_market_transactions_window", transactions.c.player_id, transactions.c.market_window_id, transactions.c.kind)

M0_TABLES = [accounts, currency, stocks, inventory, world, configs, audit, events]
M1_TABLES = [ranches, animals, products, transactions]
factories = table("processing_factories", ident("player_id", True), Column("level", Integer, nullable=False),
    Column("line_count", Integer, nullable=False), Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False), Column("rush_used_on", String(10)),
    Column("rush_count", Integer, nullable=False, server_default="0"),
    Column("automation_level", Integer, nullable=False, server_default="0"),
    CheckConstraint("level >= 1 AND level <= 5"),
    CheckConstraint("line_count >= 1 AND line_count <= 5"))
factory_jobs = table("processing_jobs", ident("id", True), ident("player_id"),
    Column("line_no", Integer, nullable=False), Column("recipe_id", String(40), nullable=False),
    Column("recipe_version", String(40), nullable=False), Column("batch_quantity", Integer, nullable=False),
    Column("started_at", Float, nullable=False), Column("finish_at", Float, nullable=False),
    Column("status", String(20), nullable=False), Column("input_snapshot_json", Text, nullable=False),
    Column("output_snapshot_json", Text, nullable=False), Column("failure_roll", String(80), nullable=False),
    Column("critical_roll", String(80), nullable=False), Column("failed", Boolean, nullable=False),
    Column("critical", Boolean, nullable=False), Column("collected_at", Float),
    Column("market_window_id", BigInteger), Column("price_snapshot_json", Text, nullable=False),
    Column("failure_rate", Numeric(8, 6)), Column("critical_rate", Numeric(8, 6)),
    Column("duration_multiplier", Numeric(12, 6)), Column("expedited_at", Float),
    Column("cancelled_at", Float), Column("cancel_refund_json", Text),
    CheckConstraint("line_no >= 1 AND batch_quantity >= 1"),
    CheckConstraint("status IN ('processing', 'completed_pending_collect', 'collected', 'failed', 'cancelled')",
                    name="ck_processing_jobs_status"))
Index("uq_processing_active_line", factory_jobs.c.player_id, factory_jobs.c.line_no, unique=True,
      sqlite_where=factory_jobs.c.status.in_(["processing", "completed_pending_collect"]),
      postgresql_where=factory_jobs.c.status.in_(["processing", "completed_pending_collect"]))
FACTORY_TABLES = [factories, factory_jobs]
buffs = table("player_buffs", ident("id", True), ident("player_id"),
    Column("buff_type", String(40), nullable=False), Column("source", String(40), nullable=False),
    Column("started_at", Float, nullable=False), Column("expires_at", Float, nullable=False),
    Column("magnitude", Numeric(12, 6), nullable=False), Column("metadata_json", Text, nullable=False),
    UniqueConstraint("player_id", "buff_type"), CheckConstraint("expires_at >= started_at"))
BUFF_TABLES = [buffs]


horse_stables = table("horse_stables", ident("player_id", True),
    Column("level", Integer, nullable=False), Column("version", Integer, nullable=False),
    Column("created_at", Float, nullable=False), Column("updated_at", Float, nullable=False),
    CheckConstraint("level >= 1 AND level <= 5"))
horses = table("horses", ident("id", True), ident("player_id"),
    Column('feed_count', Integer, nullable=False, server_default='0'),
    Column('feed_charges', Integer, nullable=False, server_default='5'),
    Column('feed_recovered_at', Float),
    Column("name", String(80)), Column("sex", String(8), nullable=False),
    Column("born_at", Float, nullable=False), Column("generation", Integer, nullable=False),
    ident("father_id"), ident("mother_id"),
    Column("speed", Integer, nullable=False), Column("stamina", Integer, nullable=False),
    Column("power", Integer, nullable=False), Column("wisdom", Integer, nullable=False),
    Column("grit", Integer, nullable=False),
    Column("growth_speed", String(1), nullable=False), Column("growth_stamina", String(1), nullable=False),
    Column("growth_power", String(1), nullable=False), Column("growth_wisdom", String(1), nullable=False),
    Column("growth_grit", String(1), nullable=False),
    Column("breeding_count", Integer, nullable=False), Column("breeding_cooldown_until", Float, nullable=False),
    Column("status", String(12), nullable=False), Column("retired_at", Float),
    Column("seed", String(64), nullable=False), Column("birth_traits_json", Text, nullable=False),
    Column("rule_version", String(40), nullable=False), Column("version", Integer, nullable=False),
    Column("created_at", Float, nullable=False),
    CheckConstraint("sex IN ('male', 'female')"),
    CheckConstraint("status IN ('active', 'retired')"),
    CheckConstraint("growth_speed IN ('U', 'S', 'A', 'B', 'C', 'D') AND growth_stamina IN ('U', 'S', 'A', 'B', 'C', 'D') AND growth_power IN ('U', 'S', 'A', 'B', 'C', 'D') AND growth_wisdom IN ('U', 'S', 'A', 'B', 'C', 'D') AND growth_grit IN ('U', 'S', 'A', 'B', 'C', 'D')"),
    CheckConstraint("generation >= 0 AND breeding_count >= 0 AND breeding_count <= 5"))
Index("ix_horses_owner_status", horses.c.player_id, horses.c.status, horses.c.created_at)
Index("uq_horses_active_name", horses.c.player_id, horses.c.name, unique=True,
      sqlite_where=horses.c.status == "active", postgresql_where=horses.c.status == "active")
horse_pregnancies = table("horse_pregnancies", ident("id", True), ident("player_id"),
    ident("mother_id"), ident("father_id"), Column("conceived_at", Float, nullable=False),
    Column("due_at", Float, nullable=False), Column("status", String(12), nullable=False),
    Column("foal_snapshot_json", Text, nullable=False), Column("rule_version", String(40), nullable=False), ident("foal_id"),
    Column("delivered_at", Float), Column("created_at", Float, nullable=False),
    CheckConstraint("status IN ('pending', 'delivered')"))
Index("uq_horse_pending_mother", horse_pregnancies.c.mother_id, unique=True,
      sqlite_where=horse_pregnancies.c.status == "pending",
      postgresql_where=horse_pregnancies.c.status == "pending")
Index("ix_horse_pregnancies_owner_due", horse_pregnancies.c.player_id,
      horse_pregnancies.c.status, horse_pregnancies.c.due_at)
horse_training_events = table("horse_training_events", ident("id", True), ident("player_id"),
    ident("horse_id"), Column("local_day", String(10), nullable=False),
    Column("feed_code", String(40), nullable=False), Column("gains_json", Text, nullable=False),
    ident("reference_id"), Column("created_at", Float, nullable=False),
    UniqueConstraint("horse_id", "reference_id"))
Index("ix_horse_training_day", horse_training_events.c.horse_id, horse_training_events.c.local_day)
HORSE_TABLES = [horse_stables, horses, horse_pregnancies, horse_training_events]

# One active slot is enforced by a partial unique index, including across Core processes.
auctions = table("auctions", ident("id", True), Column("auction_type", String(20), nullable=False),
    ident("seller_id"), ident("room_id"), Column("title", String(100), nullable=False),
    Column("description", Text), Column("asset_type", String(30)), ident("asset_id"),
    Column("asset_snapshot_json", Text), Column("starting_price", BigInteger, nullable=False),
    Column("current_price", BigInteger, nullable=False), ident("highest_bidder_id"),
    Column("starts_at", Float, nullable=False), Column("ends_at", Float, nullable=False),
    Column("status", String(20), nullable=False), Column("bid_count", Integer, nullable=False),
    Column("created_at", Float, nullable=False), Column("settled_at", Float),
    CheckConstraint("starting_price > 0 AND current_price >= starting_price"),
    CheckConstraint("status IN ('active', 'sold', 'expired', 'cancelled')"))
Index("uq_auctions_single_active", auctions.c.status, unique=True,
      sqlite_where=auctions.c.status == "active", postgresql_where=auctions.c.status == "active")
Index("ix_auctions_seller_created", auctions.c.seller_id, auctions.c.created_at)
auction_bids = table("auction_bids", ident("id", True), ident("auction_id"), ident("bidder_id"),
    Column("amount", BigInteger, nullable=False), Column("created_at", Float, nullable=False),
    Column("status", String(12), nullable=False), CheckConstraint("amount > 0"),
    CheckConstraint("status IN ('active', 'outbid', 'won')"))
Index("ix_auction_bids_bidder", auction_bids.c.bidder_id, auction_bids.c.created_at)
fund_reservations = table("fund_reservations", ident("id", True), ident("player_id"),
    Column("amount", BigInteger, nullable=False), Column("reason", String(40), nullable=False),
    Column("reference_type", String(40), nullable=False), ident("reference_id"),
    Column("status", String(12), nullable=False), Column("created_at", Float, nullable=False),
    Column("released_at", Float), UniqueConstraint("reference_type", "reference_id"),
    CheckConstraint("amount > 0"), CheckConstraint("status IN ('active', 'released', 'settled')"))
Index("ix_fund_reservations_player_active", fund_reservations.c.player_id, fund_reservations.c.status)
horse_auction_custody = table("horse_auction_custody", ident("horse_id", True), ident("auction_id"),
    ident("owner_id"), Column("status", String(12), nullable=False),
    Column("created_at", Float, nullable=False),
    CheckConstraint("status IN ('locked', 'holding')"))
AUCTION_TABLES = [auctions, auction_bids, fund_reservations, horse_auction_custody]

farms = table("farms", ident("player_id", True), Column("level", Integer, nullable=False),
    Column("version", Integer, nullable=False), Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False), CheckConstraint("level >= 1 AND level <= 9"))
farm_plots = table("farm_plots", ident("id", True), ident("player_id"),
    Column("plot_no", Integer, nullable=False), Column("crop_code", String(40)),
    ident("batch_id"), Column("planted_at", Float), Column("ready_at", Float),
    Column("yield_quantity", Integer), Column("yield_min", Integer), Column("yield_max", Integer),
    Column("base_duration_seconds", Integer), Column("weather_snapshot", String(40)),
    Column("duration_multiplier", Numeric(10, 6)), Column("rule_version", String(40)),
    Column("harvest_weather_code", String(24)), Column("harvest_weather_adjustment", Integer),
    Column("harvest_weather_rule_version", String(40)),
    Column("version", Integer, nullable=False),
    UniqueConstraint("player_id", "plot_no"), CheckConstraint("plot_no >= 1"))
Index("ix_farm_plots_owner_ready", farm_plots.c.player_id, farm_plots.c.ready_at)
horse_feed_purchases = table("horse_feed_purchases", ident("id", True), ident("player_id"),
    Column("feed_code", String(40), nullable=False), Column("quantity", Integer, nullable=False),
    Column("local_day", String(10), nullable=False), Column("created_at", Float, nullable=False),
    UniqueConstraint("player_id", "id"))
Index("ix_horse_feed_purchases_day", horse_feed_purchases.c.player_id,
      horse_feed_purchases.c.feed_code, horse_feed_purchases.c.local_day)
FARM_TABLES = [farms, farm_plots, horse_feed_purchases]

daily_weather = table("daily_weather", Column("game_date", String(10), primary_key=True),
    Column("weather_code", String(24), nullable=False),
    Column("weather_rule_version", String(40), nullable=False),
    Column("probability_config_version", String(40), nullable=False),
    Column("seed", String(64), nullable=False), Column("generated_at", Float, nullable=False),
    Column("effective_from", Float, nullable=False), Column("effective_until", Float, nullable=False),
    Column("definition_snapshot_json", Text, nullable=False), Column("broadcast_enqueued_at", Float))
Index("ix_daily_weather_effective", daily_weather.c.effective_from, daily_weather.c.effective_until)

game_updates = table("game_updates", ident("id", True),
    Column("version", String(40), nullable=False, unique=True), Column("title", String(160), nullable=False),
    Column("theme", String(40), nullable=False), Column("summary", String(500), nullable=False),
    Column("content", Text, nullable=False), Column("status", String(20), nullable=False),
    Column("created_at", Float, nullable=False), Column("published_at", Float),
    Column("created_by", String(200), nullable=False), Column("manifest_json", Text, nullable=False),
    Column("changelog_json", Text, nullable=False),
    CheckConstraint("status IN ('draft', 'published', 'cancelled')"))
broadcast_jobs = table("broadcast_jobs", ident("id", True), ident("update_id"),
    Column("type", String(30), nullable=False), Column("status", String(20), nullable=False),
    Column("target_count", Integer, nullable=False), Column("success_count", Integer, nullable=False),
    Column("failure_count", Integer, nullable=False), Column("created_at", Float, nullable=False),
    Column("started_at", Float), Column("finished_at", Float), Column("content", Text, nullable=False),
    Column("scope", String(20), nullable=False),
    CheckConstraint("status IN ('queued', 'running', 'completed', 'partial_failed', 'failed')"))
Index("ix_broadcast_jobs_update_created", broadcast_jobs.c.update_id, broadcast_jobs.c.created_at)
broadcast_deliveries = table("broadcast_deliveries", ident("id", True),
    Column("broadcast_job_id", String(200), nullable=False), Column("room_id", String(200), nullable=False),
    Column("status", String(20), nullable=False), Column("attempt_count", Integer, nullable=False),
    Column("last_error", String(300)), Column("sent_at", Float), Column("created_at", Float, nullable=False),
    Column("outbox_id", String(200), unique=True),
    UniqueConstraint("broadcast_job_id", "room_id"),
    CheckConstraint("status IN ('queued', 'sending', 'success', 'failed')"))
Index("ix_broadcast_delivery_job_status", broadcast_deliveries.c.broadcast_job_id, broadcast_deliveries.c.status)
player_feature_tips = table("player_feature_tips", ident("player_id"), ident("feature_id"),
    Column("shown_at", Float, nullable=False), Column("dismissed", Boolean, nullable=False, server_default=false()),
    Column("completed", Boolean, nullable=False, server_default=false()))
world_event_log = table("world_event_log", ident("id", True),
    Column("event_type", String(60), nullable=False), ident("player_id"), ident("room_id"),
    Column("payload_json", Text, nullable=False), Column("created_at", Float, nullable=False),
    Column("broadcasted_at", Float), Column("scope", String(20), nullable=False),
    Column("broadcast_job_id", String(200)))
Index("ix_world_event_player_cooldown", world_event_log.c.event_type, world_event_log.c.player_id,
      world_event_log.c.created_at)
Index("ix_world_event_global_cooldown", world_event_log.c.event_type, world_event_log.c.created_at)

INFORMATION_TABLES = [game_updates, broadcast_jobs, broadcast_deliveries, player_feature_tips, world_event_log]

pending_group_invites = table("pending_group_invites", ident("id", True),
    ident("source_room_id"), ident("source_message_id"), ident("inviter_id"),
    Column("invite_code", String(100), nullable=False), Column("group_name", String(160), nullable=False),
    Column("source_content_json", Text),
    Column("status", String(20), nullable=False), Column("created_at", Float, nullable=False),
    Column("approved_at", Float), Column("joined_at", Float), ident("joined_room_id"),
    Column("last_error", String(200)),
    UniqueConstraint("source_room_id", "source_message_id"),
    CheckConstraint("status IN ('pending', 'approved', 'joining', 'joined', 'failed', 'rejected')"))
Index("ix_pending_group_invites_status", pending_group_invites.c.status, pending_group_invites.c.created_at)
