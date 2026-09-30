"""Private-chat authentication for game-chat administrator commands."""
import hashlib
import hmac
import json
import uuid

from sqlalchemy import func, select

from dzmm_bot.persistence.schema import accounts, audit, game_admin_members


def verify_admin_password(candidate, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        count = int(iterations)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= count <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"),
                                     bytes.fromhex(salt), count)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, UnicodeError):
        return False


class AdminLoginService:
    MAX_FAILURES = 5
    WINDOW_SECONDS = 3600

    def __init__(self, db, now, password_hash, static_admins=()):
        self.db, self.now, self.password_hash = db, now, password_hash
        self.static_admins = set(static_admins)

    def is_admin(self, player_id):
        return player_id in self.static_admins or bool(self.db.execute(
            select(game_admin_members.c.player_id).where(
                game_admin_members.c.player_id == player_id)).first())

    def login(self, player_id, room_kind, parts):
        if room_kind != "private":
            return "⚠️ 管理员登录仅可在 Bot 私聊中使用。若已在群里发送密码，请立即更换。"
        if len(parts) != 2:
            return "⚠️ 指令格式：/管理员登陆 <密码>"
        if not self.password_hash:
            return "⚠️ 管理员登录尚未配置。"
        if not self.db.execute(select(accounts.c.player_id).where(
                accounts.c.player_id == player_id)).first():
            return "⚠️ 请先在游戏群注册账号。"
        if self.is_admin(player_id):
            return "✅ 你已具有游戏管理员权限。"
        failures = self.db.execute(select(func.count()).select_from(audit).where(
            audit.c.actor == player_id, audit.c.action == "game_admin_login_failed",
            audit.c.created_at >= self.now - self.WINDOW_SECONDS)).scalar_one()
        if failures >= self.MAX_FAILURES:
            return "⚠️ 登录尝试过多，请一小时后再试。"
        if not verify_admin_password(parts[1], self.password_hash):
            self._audit(player_id, "game_admin_login_failed")
            return "⚠️ 管理员密码错误。"
        self.db.execute(game_admin_members.insert().values(
            player_id=player_id, granted_at=self.now, grant_source="private_password"))
        self._audit(player_id, "game_admin_login_success")
        return "✅ 已获得游戏管理员权限。"

    def _audit(self, player_id, action):
        self.db.execute(audit.insert().values(id=str(uuid.uuid4()), actor=player_id,
            action=action, target_type="player", target_id=player_id,
            payload_json=json.dumps({}), created_at=self.now))
