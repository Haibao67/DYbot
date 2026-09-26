"""Default application: local Core, not a public webhook."""
from dzmm_bot.core import app, create_app

__all__ = ["app", "create_app"]
