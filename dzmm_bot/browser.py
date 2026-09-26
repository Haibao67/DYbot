"""Dedicated persistent browser. Credentials never leave this process/profile."""
import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.async_api import async_playwright
from .settings import ROOT


def account_id(body, field=""):
    """Accept known RPC envelopes only; unresolved schemas fail closed."""
    if field:
        value = body
        for part in field.split("."):
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value if isinstance(value, str) and value else None
    if isinstance(body, list) and len(body) == 1:
        body = body[0]
    for key in ("result", "data", "json"):
        if isinstance(body, dict) and key in body:
            body = body[key]
    if isinstance(body, dict):
        value = body.get("id")
        return value if isinstance(value, str) and value else None
    return None


def account_response(body, path):
    """Select only user.getMe from an actual tRPC batch response."""
    procedures = unquote(path.rsplit("/", 1)[-1]).split(",")
    if "user.getMe" not in procedures:
        return None
    if isinstance(body, list):
        index = procedures.index("user.getMe")
        if len(body) != len(procedures):
            return None
        body = body[index]
    elif len(procedures) != 1:
        return None
    for key in ("result", "data", "json"):
        if isinstance(body, dict) and key in body:
            body = body[key]
    return body if isinstance(body, dict) else None


class BrowserSession:
    def __init__(self, settings, headed=False):
        self.cfg, self.headed = settings, headed
        self.user_id = None
        self.logged_out = False

    async def __aenter__(self):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "data" / "browsers"))
        self.playwright = await async_playwright().start()
        try:
            channel = self.cfg.browser_channel or "chromium"
            if channel not in {"chromium", "chrome", "msedge"}:
                raise ValueError("DZMM_BROWSER_CHANNEL must be chromium, chrome or msedge")
            options = {"channel": channel}
            self.context = await self.playwright.chromium.launch_persistent_context(
                self.cfg.profile, headless=not self.headed, **options)
            await self.restore_login_state()
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.page.on("response", self.observe_account)
            await self.page.goto(self.cfg.login_url if self.headed else self.cfg.chat_url,
                                 wait_until="domcontentloaded", timeout=45000)
        except BaseException:
            if hasattr(self, "context"):
                await self.context.close()
            await self.playwright.stop()
            raise
        return self

    async def __aexit__(self, *args):
        with suppress(Exception):
            await self.save_login_state()
        with suppress(Exception):
            await self.context.close()
        with suppress(Exception):
            await self.playwright.stop()

    @property
    def state_path(self):
        return Path(self.cfg.profile) / ".auth-state.json"

    async def restore_login_state(self):
        """Restore only same-site cookies from the protected browser profile."""
        if not self.state_path.is_file():
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            host = urlsplit(self.cfg.chat_url).hostname
            cookies = [cookie for cookie in state.get("cookies", [])
                       if isinstance(cookie, dict) and host and
                       cookie.get("domain", "").lstrip(".") in {host, host.removeprefix("www.")}]
            if cookies:
                await self.context.add_cookies(cookies)
        except (OSError, ValueError, TypeError):
            # A corrupt snapshot must never prevent manual login recovery.
            return

    async def save_login_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        await self.context.storage_state(path=str(self.state_path))
        os.chmod(self.state_path, 0o600)

    async def observe_account(self, response):
        url = urlsplit(response.url)
        if f"{url.scheme}://{url.netloc}" != self.cfg.origin or "user.getMe" not in url.path:
            return
        try:
            if 200 <= response.status < 300:
                body = account_response(await response.json(), url.path)
                if body and body.get("isLoggedIn") is False:
                    self.logged_out = True
                    self.user_id = None
                    return
                value = account_id(body)
                if value:
                    self.user_id = value
                    self.logged_out = False
        except Exception:
            pass

    async def credentials(self):
        # Every reconnect refreshes the page and short-lived access token.
        self.user_id = None
        self.logged_out = False
        await self.page.goto(self.cfg.chat_url, wait_until="domcontentloaded", timeout=45000)
        if not self.page.url.startswith(self.cfg.origin + "/"):
            raise RuntimeError("login_required")
        if not self.cfg.me_path:
            # SPA requests can complete after DOMContentLoaded. Do not wait for
            # networkidle, because live chat pages may never become idle.
            for _ in range(50):
                if self.user_id or self.logged_out:
                    break
                await asyncio.sleep(0.1)
        if self.cfg.me_path:
            if not self.cfg.me_path.startswith("/") or self.cfg.me_path.startswith("//"):
                raise RuntimeError("DZMM_ME_PATH 必须是本站相对路径")
            body = await self.page.evaluate("""async path => {
                const r = await fetch(path, {credentials:'include'});
                if (!r.ok) return null;
                return await r.json();
            }""", self.cfg.me_path)
            try:
                self.user_id = account_id(body, self.cfg.me_id_field)
            except (KeyError, TypeError, IndexError, ValueError):
                self.user_id = None
        if self.logged_out:
            raise RuntimeError("login_required: 请在当前 DZMM 域名完成专用浏览器登录")
        if not self.user_id:
            raise RuntimeError("account_identity_unresolved: 需要核对 user.getMe 接口及返回结构")
        body = await self.page.evaluate("""async () => {
            const r = await fetch('/api/auth/token', {credentials:'include'});
            if (!r.ok) return null;
            return await r.json();
        }""")
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("login_required_or_token_schema_changed")
        await self.save_login_state()
        # Socket now runs inside Chromium and uses its own cookie jar.
        return token, self.user_id


async def login(settings):
    async with BrowserSession(settings, headed=True):
        print("请在专用浏览器中手动登录并打开一个已加入的群。完成后回到此窗口按 Enter 保存并关闭。")
        await asyncio.to_thread(input)
