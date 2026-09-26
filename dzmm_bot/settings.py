import os
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

    @property
    def origin(self):
        url = urlsplit(self.chat_url)
        if url.scheme != "https" or not url.netloc:
            raise ValueError("DZMM_CHAT_URL 必须是 HTTPS 地址")
        return f"{url.scheme}://{url.netloc}"

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        return cls(**{key: os.getenv(env, getattr(cls(), key)) for key, env in {
            "database_url": "DZMM_DATABASE_URL", "core_url": "DZMM_CORE_URL",
            "core_token": "DZMM_CORE_TOKEN", "admin_token": "DZMM_ADMIN_TOKEN",
            "profile": "DZMM_BROWSER_PROFILE", "login_url": "DZMM_LOGIN_URL",
            "chat_url": "DZMM_CHAT_URL", "bot_token": "DZMM_BOT_API_TOKEN",
            "bot_id": "DZMM_BOT_ID", "mode": "DZMM_WORKER_MODE",
            "me_path": "DZMM_ME_PATH", "me_id_field": "DZMM_ME_ID_FIELD",
            "browser_channel": "DZMM_BROWSER_CHANNEL",
            "game_stage": "DZMM_GAME_STAGE", "game_whitelist": "DZMM_GAME_WHITELIST",
        }.items()})
