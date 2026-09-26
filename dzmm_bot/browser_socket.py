"""Socket.IO transport inside the authenticated Chromium context.

Only validated inbound data crosses the existing Gateway/Core boundary. Browser
cookies stay in Chromium; this avoids a second Python HTTP/TLS network path.
"""
from pathlib import Path

from socketio.exceptions import ConnectionError, TimeoutError


class BrowserSocket:
    def __init__(self, browser):
        self.browser = browser
        self.connected = False
        self.handlers = {}
        self.generation = 0
        self.bound_page = None

    def on(self, event, handler):
        self.handlers[event] = handler

    async def _event(self, source, event, data, generation):
        if generation != self.generation or source["page"] is not self.browser.page:
            return
        if event == "disconnect":
            self.connected = False
        handler = self.handlers.get(event)
        if handler:
            await handler(data)

    async def connect(self, url, *, socketio_path, auth, wait_timeout=20):
        page = self.browser.page
        self.connected = False
        self.generation += 1
        if self.bound_page is not page:
            await page.expose_binding("__dzmmWorkerEvent", self._event)
            self.bound_page = page
        vendor = Path(__file__).parent / "vendor" / "socket.io.min.js"
        if not vendor.exists():
            raise RuntimeError("Socket.IO client missing; run install_socket_client.py")
        source = vendor.read_text(encoding="utf-8")
        # UMD CommonJS branch keeps the library separate from the site's own io.
        await page.evaluate("() => { const module = {exports:{}}; const exports = module.exports;\n" +
                            source + "\nwindow.__dzmmWorkerIO = module.exports; }")
        try:
            result = await page.evaluate("""({url, path, auth, timeout, generation}) => new Promise(resolve => {
                if (window.__dzmmWorkerSocket) window.__dzmmWorkerSocket.disconnect();
                const socket = window.__dzmmWorkerIO(url, {
                    path, auth, transports:['websocket'], reconnection:false,
                    forceNew:true, autoConnect:false, timeout
                });
                window.__dzmmWorkerSocket = socket;
                const notify = (event, data) => window.__dzmmWorkerEvent(event, data, generation).catch(() => {});
                socket.on('message:new', data => notify('message:new', data));
                socket.on('disconnect', () => notify('disconnect', null));
                const timer = setTimeout(() => { socket.disconnect(); resolve(false); }, timeout + 1000);
                socket.once('connect', () => { clearTimeout(timer); resolve(true); });
                socket.once('connect_error', () => { clearTimeout(timer); socket.disconnect(); resolve(false); });
                socket.connect();
            })""", {"url": url, "path": "/" + socketio_path.strip("/"), "auth": auth,
                       "timeout": int(wait_timeout * 1000), "generation": self.generation})
        except Exception:
            raise ConnectionError("Browser Socket connection interrupted") from None
        self.connected = bool(result)
        if not self.connected:
            raise ConnectionError("Browser Socket authentication or connection failed")

    async def emit(self, event, data):
        try:
            sent = await self.browser.page.evaluate("""({event, data}) => {
                const socket = window.__dzmmWorkerSocket;
                if (!socket || !socket.connected) return false;
                socket.emit(event, data); return true;
            }""", {"event": event, "data": data})
        except Exception:
            self.connected = False
            raise ConnectionError("Browser Socket unavailable") from None
        if not sent:
            self.connected = False
            raise ConnectionError("Browser Socket disconnected")

    async def call(self, event, data, timeout=15):
        try:
            result = await self.browser.page.evaluate("""({event, data, timeout}) => new Promise(resolve => {
                const socket = window.__dzmmWorkerSocket;
                if (!socket || !socket.connected) return resolve({ok:false});
                socket.timeout(timeout).emit(event, data, (error, ack) => {
                    resolve(error ? {ok:false} : {ok:true, ack});
                });
            })""", {"event": event, "data": data, "timeout": int(timeout * 1000)})
        except Exception:
            self.connected = False
            raise ConnectionError("Browser Socket result unknown") from None
        if not result.get("ok"):
            raise TimeoutError("Browser Socket acknowledgement unavailable")
        return result.get("ack")

    async def disconnect(self):
        self.connected = False
        self.generation += 1
        try:
            await self.browser.page.evaluate("() => { if (window.__dzmmWorkerSocket) window.__dzmmWorkerSocket.disconnect(); }")
        except Exception:
            pass
