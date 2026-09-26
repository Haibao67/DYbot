"""Archived webhook prototype; default entry point now runs Core."""
import asyncio
import hmac
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

load_dotenv()


def create_app(db_path=None, secret=None, token=None, dry_run=None, transport=None):
    secret = secret if secret is not None else os.getenv("DZMM_BOT_SECRET", "")
    token = token if token is not None else os.getenv("DZMM_BOT_TOKEN", "")
    dry_run = dry_run if dry_run is not None else os.getenv("BOT_DRY_RUN", "true").lower() == "true"
    db_path = str(db_path or os.getenv("BOT_DB_PATH", "data/bot.sqlite3"))
    api_base = os.getenv("BOT_API_BASE", "https://dzmm.io").rstrip("/")
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if not secret:
            raise RuntimeError("请配置 DZMM_BOT_SECRET；参考 .env.example")
        if not dry_run and (not token or secret == "local-test-secret"):
            raise RuntimeError("真实模式需要平台 API Token 和真实 Webhook Secret")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db_path)) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS players (
                    user_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memberships (
                    chat_id TEXT NOT NULL, user_id TEXT NOT NULL, joined_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id));
                CREATE TABLE IF NOT EXISTS events (
                    chat_id TEXT NOT NULL, message_id TEXT NOT NULL, reply TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, message_id));
            ''')
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            app.state.client = client
            yield

    app = FastAPI(title="群聊游戏测试 Bot", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"ok": True, "mode": "dry_run" if dry_run else "live"}

    @app.post("/webhook")
    async def webhook(request: Request):
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secret or not hmac.compare_digest(supplied.encode(), secret.encode()):
            raise HTTPException(401, "unauthorized")
        try:
            update = await request.json()
        except ValueError:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(update, dict):
            raise HTTPException(400, "expected JSON object")
        message = update.get("message")
        if not isinstance(message, dict):
            return {"ok": True, "ignored": True}
        chat, sender = message.get("chat"), message.get("from")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return {"ok": True, "ignored": True}
        if sender.get("is_bot") or not isinstance(text, str) or not text.strip().startswith("/"):
            return {"ok": True, "ignored": True}
        chat_id, user_id, message_id = chat.get("id"), sender.get("id"), message.get("message_id")
        if not all(isinstance(value, str) and value for value in (chat_id, user_id, message_id)):
            raise HTTPException(400, "chat, user and message IDs must be nonempty strings")
        command = text.strip().split()[0].lower()
        if command not in {"/start", "/help", "/join", "/profile"}:
            return {"ok": True, "ignored": True}
        name = str(sender.get("first_name") or "玩家")[:100]
        # Serial processing is intentional for this single-worker test service.
        async with lock:
            with closing(sqlite3.connect(db_path)) as db:
                event = db.execute("SELECT reply, sent FROM events WHERE chat_id=? AND message_id=?", (chat_id, message_id)).fetchone()
                if event and event[1]:
                    return {"ok": True, "duplicate": True}
                if event:
                    reply = event[0]
                else:
                    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if command in {"/start", "/help"}:
                        reply = "欢迎来到游戏测试 Bot！\n/start 开始\n/help 帮助\n/join 加入当前群的游戏\n/profile 查看个人资料\n游戏玩法稍后开放。"
                    elif command == "/join":
                        db.execute("INSERT INTO players VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name", (user_id, name, now))
                        inserted = db.execute("INSERT OR IGNORE INTO memberships VALUES (?, ?, ?)", (chat_id, user_id, now)).rowcount
                        reply = f"{name}，{'成功加入当前群的游戏！' if inserted else '你已经加入当前群的游戏。'}\n发送 /profile 查看资料。"
                    else:
                        player = db.execute("SELECT p.created_at, m.joined_at FROM players p JOIN memberships m ON p.user_id=m.user_id WHERE p.user_id=? AND m.chat_id=?", (user_id, chat_id)).fetchone()
                        reply = (f"玩家档案\n昵称：{name}\n玩家 ID：{user_id}\n当前群 ID：{chat_id}\n注册时间（UTC）：{player[0]}\n入群游戏时间（UTC）：{player[1]}" if player else "你尚未加入当前群的游戏，请先发送 /join。")
                    db.execute("INSERT INTO events (chat_id, message_id, reply) VALUES (?, ?, ?)", (chat_id, message_id, reply))
                db.commit()
                if not dry_run:
                    try:
                        response = await app.state.client.post(api_base + "/api/bot/send-message", headers={"X-Bot-Token": token}, json={"chatroom_id": chat_id, "content": reply})
                        response.raise_for_status()
                        body = response.json()
                        if not isinstance(body, dict) or body.get("ok") is not True:
                            raise ValueError("platform rejected message")
                    except (httpx.HTTPError, ValueError):
                        # Do not expose credentials or upstream response bodies.
                        raise HTTPException(502, "发送失败，请检查平台连接、Token 和 Bot 群成员状态")
                db.execute("UPDATE events SET sent=1 WHERE chat_id=? AND message_id=?", (chat_id, message_id))
                db.commit()
            result = {"ok": True}
            if dry_run:
                result.update(dry_run=True, reply=reply, chatroom_id=chat_id)
            return result

    return app


app = create_app()
