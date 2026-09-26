"""Local admin CLI; credentials are never printed."""
import argparse
import asyncio
import secrets

import httpx
from dotenv import dotenv_values, set_key

from .settings import ROOT, Settings


def initialize():
    path = ROOT / ".env"
    existing = dotenv_values(path) if path.exists() else {}
    defaults = {
        "DZMM_CORE_TOKEN": secrets.token_urlsafe(32), "DZMM_ADMIN_TOKEN": secrets.token_urlsafe(32),
        "DZMM_DATABASE_URL": "sqlite:///data/core.sqlite3", "DZMM_CORE_URL": "http://127.0.0.1:18120",
        "DZMM_WORKER_MODE": "simulate", "DZMM_BROWSER_PROFILE": "data/browser-profile",
        "DZMM_BROWSER_CHANNEL": "chromium",
        "DZMM_LOGIN_URL": "https://www.ivorune.xyz/sign-in", "DZMM_CHAT_URL": "https://www.ivorune.xyz/chat",
        "DZMM_BOT_API_TOKEN": "", "DZMM_BOT_ID": "", "DZMM_ME_PATH": "", "DZMM_ME_ID_FIELD": "id",
        "DZMM_GAME_STAGE": "full", "DZMM_GAME_WHITELIST": "",
    }
    for key, value in defaults.items():
        if key not in existing or (key in {"DZMM_CORE_TOKEN", "DZMM_ADMIN_TOKEN"} and not existing[key]):
            set_key(str(path), key, value)
    print("配置已补齐；原配置和旧玩家数据库保留。密钥已写入 .env，不会显示。")


def main():
    parser = argparse.ArgumentParser(description="DZMM 测试 Bot 管理")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "login", "status"):
        commands.add_parser(name)
    room = commands.add_parser("room")
    room.add_argument("id")
    room.add_argument("--kind", choices=["group", "private"], default="group")
    room.add_argument("--disable", action="store_true")
    simulation = commands.add_parser("simulate")
    simulation.add_argument("text", nargs="?", default="/加入")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("id")
    resolve.add_argument("--as", dest="resolution", choices=["sent", "failed"], required=True)
    resolve.add_argument("--platform-id")
    args = parser.parse_args()
    if args.command == "init":
        initialize()
        return
    cfg = Settings.load()
    if args.command == "login":
        from .browser import login
        asyncio.run(login(cfg))
        return
    with httpx.Client(base_url=cfg.core_url, timeout=20, headers={"X-Admin-Token": cfg.admin_token}) as client:
        if args.command == "room":
            response = client.put("/admin/rooms", json={"id": args.id, "kind": args.kind, "enabled": not args.disable})
        elif args.command == "status":
            response = client.get("/admin/status")
        elif args.command == "resolve":
            response = client.post(f"/admin/outbound/{args.id}/resolve", json={"status": args.resolution, "platform_id": args.platform_id})
        else:
            if cfg.mode != "simulate":
                raise SystemExit("模拟命令仅允许在 DZMM_WORKER_MODE=simulate 时使用")
            response = client.post("/admin/simulate", json={"text": args.text})
        response.raise_for_status()
        print(response.json())


if __name__ == "__main__":
    main()
