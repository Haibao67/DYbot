"""Local browser smoke check; does not open DZMM or use the login profile."""
import asyncio
import tempfile

from dzmm_bot.browser import BrowserSession
from dzmm_bot.settings import ROOT, Settings


async def main():
    with tempfile.TemporaryDirectory(prefix="chromium-check-", dir=ROOT / "data") as profile:
        cfg = Settings(profile=profile, chat_url="about:blank", browser_channel="chromium")
        async with BrowserSession(cfg) as browser:
            await browser.page.set_content("<title>Bot browser check</title><p>Chromium ready</p>")
            assert await browser.page.title() == "Bot browser check"
            assert await browser.page.evaluate("1 + 1") == 2
            print("PASS: Chromium launched, local page rendered, JavaScript executed")
            print("Browser version:", browser.context.browser.version)


if __name__ == "__main__":
    asyncio.run(main())
