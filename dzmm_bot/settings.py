import os
import math
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    database_url: str = "sqlite:///data/core.sqlite3"
    core_url: str = "http://127.0.0.1:18120"
    core_token: str = field(default="", repr=False)
    admin_token: str = field(default="", repr=False)
    profile: str = str(ROOT / "data" / "browser-profile")
    login_url: str = "https://www.ivorune.xyz/sign-in"
    chat_url: str = "https://www.ivorune.xyz/chat"
    bot_token: str = field(default="", repr=False)
    bot_id: str = ""
    mode: str = "simulate"
    # Supplied guide names user.getMe but omits its HTTP route/response envelope.
    me_path: str = ""
    me_id_field: str = "id"
    browser_channel: str = "chromium"
    game_stage: str = "full"
    game_whitelist: str = ""
    game_admins: str = ""
    game_admin_login_password_hash: str = field(default="", repr=False)
    auction_start_cooldown_seconds: int = 300
    race_auto_enabled: bool = True
    race_timezone: str = 'Asia/Shanghai'
    race_commentary_ai_enabled: bool = False
    deepseek_api_key: str = field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    race_commentary_timeout_seconds: int = 10
    race_commentary_max_chars: int = 250
    race_commentary_max_calls_per_race: int = 20
    weather_enabled: bool = False
    world_harvest_threshold: int = 0
    world_event_player_cooldown: int = 0
    world_event_global_cooldown: int = 0
    socket_initial_interval: float = 0.2
    socket_min_interval: float = 0.2
    socket_max_interval: float = 60.0
    socket_private_interval: float = 5.0
    socket_success_window: int = 30
    socket_success_step: float = 0.1
    socket_rate_cooldown: float = 60.0
    socket_backoff_factor: float = 2.0
    outbound_inflight_limit: int = 1

    def __post_init__(self):
        from zoneinfo import ZoneInfo
        ZoneInfo(self.race_timezone)
        numbers = (self.socket_initial_interval, self.socket_min_interval,
                   self.socket_max_interval, self.socket_private_interval,
                   self.socket_success_step, self.socket_rate_cooldown,
                   self.socket_backoff_factor)
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in numbers):
            raise ValueError("Socket interval settings must be finite numbers")
        if not 0 < self.socket_min_interval <= self.socket_initial_interval <= self.socket_max_interval <= 60:
            raise ValueError("Socket interval settings require 0 < min <= initial <= max <= 60")
        if not 0 < self.socket_private_interval <= 60:
            raise ValueError("Socket private interval must be between 0 and 60 seconds")
        if not isinstance(self.socket_success_window, int) or not 1 <= self.socket_success_window <= 1000:
            raise ValueError("Socket success window must be between 1 and 1000")
        if not 0 < self.socket_success_step <= 1:
            raise ValueError("Socket success step must be between 0 and 1 seconds")
        if not 60 <= self.socket_rate_cooldown <= 300:
            raise ValueError("Socket rate cooldown must be between 60 and 300 seconds")
        if not 1 < self.socket_backoff_factor <= 4:
            raise ValueError("Socket backoff factor must be between 1 and 4")
        if not isinstance(self.outbound_inflight_limit, int) or not 1 <= self.outbound_inflight_limit <= 4:
            raise ValueError("Outbound in-flight limit must be between 1 and 4")
        if not isinstance(self.auction_start_cooldown_seconds, int) or not 0 <= self.auction_start_cooldown_seconds <= 86400:
            raise ValueError("Auction start cooldown must be between 0 and 86400 seconds")
        if type(self.race_commentary_ai_enabled) is not bool:
            raise ValueError("DZMM_RACE_COMMENTARY_AI_ENABLED must be a bool")
        if not isinstance(self.race_commentary_timeout_seconds, int) or not 1 <= self.race_commentary_timeout_seconds <= 10:
            raise ValueError("DZMM_RACE_COMMENTARY_TIMEOUT_SECONDS must be between 1 and 10")
        if not isinstance(self.race_commentary_max_chars, int) or not 1 <= self.race_commentary_max_chars <= 250:
            raise ValueError("DZMM_RACE_COMMENTARY_MAX_CHARS must be between 1 and 250")
        if not isinstance(self.race_commentary_max_calls_per_race, int) or not 1 <= self.race_commentary_max_calls_per_race <= 20:
            raise ValueError("DZMM_RACE_COMMENTARY_MAX_CALLS_PER_RACE must be between 1 and 20")
        commentary_url = urlsplit(self.deepseek_base_url)
        if commentary_url.scheme != "https" or not commentary_url.netloc or not self.deepseek_model.strip():
            raise ValueError("DeepSeek base URL must be HTTPS and model must be non-empty")

    @property
    def origin(self):
        url = urlsplit(self.chat_url)
        if url.scheme != "https" or not url.netloc:
            raise ValueError("DZMM_CHAT_URL 必须是 HTTPS 地址")
        return f"{url.scheme}://{url.netloc}"

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        mapping = {
            "database_url": "DZMM_DATABASE_URL", "core_url": "DZMM_CORE_URL",
            "core_token": "DZMM_CORE_TOKEN", "admin_token": "DZMM_ADMIN_TOKEN",
            "profile": "DZMM_BROWSER_PROFILE", "login_url": "DZMM_LOGIN_URL",
            "chat_url": "DZMM_CHAT_URL", "bot_token": "DZMM_BOT_API_TOKEN",
            "bot_id": "DZMM_BOT_ID", "mode": "DZMM_WORKER_MODE",
            "me_path": "DZMM_ME_PATH", "me_id_field": "DZMM_ME_ID_FIELD",
            "browser_channel": "DZMM_BROWSER_CHANNEL",
            "game_stage": "DZMM_GAME_STAGE", "game_whitelist": "DZMM_GAME_WHITELIST",
            "game_admins": "DZMM_GAME_ADMINS",
            "game_admin_login_password_hash": "DZMM_GAME_ADMIN_LOGIN_PASSWORD_HASH",
            "auction_start_cooldown_seconds": "DZMM_AUCTION_START_COOLDOWN_SECONDS",
            "race_auto_enabled": "DZMM_RACE_AUTO_ENABLED", "race_timezone": "DZMM_RACE_TIMEZONE",
            "race_commentary_ai_enabled": "DZMM_RACE_COMMENTARY_AI_ENABLED",
            "deepseek_api_key": "DEEPSEEK_API_KEY", "deepseek_base_url": "DEEPSEEK_BASE_URL",
            "deepseek_model": "DEEPSEEK_MODEL",
            "race_commentary_timeout_seconds": "DEEPSEEK_TIMEOUT_SECONDS",
            "race_commentary_max_chars": "DZMM_RACE_COMMENTARY_MAX_CHARS",
            "race_commentary_max_calls_per_race": "DZMM_RACE_COMMENTARY_MAX_CALLS_PER_RACE",
            "weather_enabled": "DZMM_WEATHER_ENABLED",
            "world_harvest_threshold": "DZMM_WORLD_HARVEST_THRESHOLD",
            "world_event_player_cooldown": "DZMM_WORLD_EVENT_PLAYER_COOLDOWN",
            "world_event_global_cooldown": "DZMM_WORLD_EVENT_GLOBAL_COOLDOWN",
            "socket_initial_interval": "DZMM_SOCKET_INITIAL_INTERVAL",
            "socket_min_interval": "DZMM_SOCKET_MIN_INTERVAL",
            "socket_max_interval": "DZMM_SOCKET_MAX_INTERVAL",
            "socket_private_interval": "DZMM_SOCKET_PRIVATE_INTERVAL",
            "socket_success_window": "DZMM_SOCKET_SUCCESS_WINDOW",
            "socket_success_step": "DZMM_SOCKET_SUCCESS_STEP",
            "socket_rate_cooldown": "DZMM_SOCKET_RATE_COOLDOWN",
            "socket_backoff_factor": "DZMM_SOCKET_BACKOFF_FACTOR",
            "outbound_inflight_limit": "DZMM_OUTBOUND_INFLIGHT_LIMIT",
        }
        defaults = cls()
        values = {}
        for key, env in mapping.items():
            raw = os.getenv(env)
            if raw is None:
                values[key] = getattr(defaults, key)
            elif key in ('race_auto_enabled', 'weather_enabled', 'race_commentary_ai_enabled'):
                if raw.lower() not in ('true','false','1','0'):
                    raise ValueError(f'{env} must be true/false or 1/0')
                values[key]=raw.lower() in ('true','1')
            elif key.startswith("socket_") or key in ("outbound_inflight_limit", "auction_start_cooldown_seconds",
                    "race_commentary_timeout_seconds", "race_commentary_max_chars", "race_commentary_max_calls_per_race"):
                try:
                    values[key] = int(raw) if key in ("socket_success_window", "outbound_inflight_limit", "auction_start_cooldown_seconds",
                        "race_commentary_timeout_seconds", "race_commentary_max_chars", "race_commentary_max_calls_per_race") else float(raw)
                except ValueError as exc:
                    raise ValueError(f"{env} must be numeric") from exc
            else:
                values[key] = raw
        return cls(**values)
