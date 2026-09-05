from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from aiohttp import web
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app_version import APP_VERSION
from db import init_db
from vinted_probe import DEFAULT_BASE_URL
from vinted_session_store import (
    finish_session_ticket,
    get_session_ticket,
    publish_session_service,
    save_vinted_session,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("vinted-session-worker")

VIEWPORT_W = 1280
VIEWPORT_H = 820
CAPTURE_TTL_SECONDS = 15 * 60
MAX_ACTIVE_CAPTURES = 2


def _public_url() -> str:
    explicit = (os.getenv("VINTED_SESSION_PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    for key in ("RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL"):
        value = (os.getenv(key) or "").strip().rstrip("/")
        if value:
            return value if value.startswith("http://") or value.startswith("https://") else f"https://{value}"
    return ""


@dataclass
class Capture:
    token: str
    admin_user_id: int
    created_mono: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pw: Playwright | None = None
    browser: Browser | None = None
    context: BrowserContext | None = None
    page: Page | None = None
    status: str = "starting"
    detail: str = ""

    async def start(self) -> None:
        async with self.lock:
            if self.page is not None:
                return
            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self.context = await self.browser.new_context(
                locale="de-DE",
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
            )
            self.page = await self.context.new_page()
            base = (os.getenv("VINTED_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
            try:
                await self.page.goto(f"{base}/member/general/login", wait_until="domcontentloaded", timeout=35_000)
            except Exception:
                await self.page.goto(base, wait_until="domcontentloaded", timeout=35_000)
            self.status = "ready"
            self.detail = "Окно Vinted готово"

    async def close(self) -> None:
        async with self.lock:
            page, context, browser, pw = self.page, self.context, self.browser, self.pw
            self.page = None
            self.context = None
            self.browser = None
            self.pw = None
            for obj, method in ((page, "close"), (context, "close"), (browser, "close"), (pw, "stop")):
                if obj is None:
                    continue
                try:
                    await getattr(obj, method)()
                except Exception:
                    pass

    async def login_state(self) -> dict[str, Any]:
        if self.context is None or self.page is None:
            return {"logged_in": False, "url": "", "title": "", "status": self.status, "detail": self.detail}
        cookies = await self.context.cookies()
        try:
            current = await self.page.evaluate(
                """
                async () => {
                  try {
                    const response = await fetch('/api/v2/users/current', {
                      credentials: 'include',
                      headers: {'Accept': 'application/json, text/plain, */*'}
                    });
                    let data = null;
                    try { data = await response.json(); } catch (_) {}
                    const user = data && (data.user || data.current_user || data);
                    const id = user && (user.id || user.user_id);
                    return {status: response.status, user_id: id ? String(id) : ''};
                  } catch (error) {
                    return {status: 0, user_id: ''};
                  }
                }
                """
            )
        except Exception:
            current = {"status": 0, "user_id": ""}
        logged_in = int(current.get("status") or 0) == 200 and bool(str(current.get("user_id") or ""))
        try:
            title = await self.page.title()
        except Exception:
            title = ""
        return {
            "logged_in": logged_in,
            "user_id": str(current.get("user_id") or ""),
            "current_user_status": int(current.get("status") or 0),
            "url": self.page.url,
            "title": title[:120],
            "status": self.status,
            "detail": self.detail,
            "cookie_count": len(cookies),
        }

    async def save(self) -> dict[str, Any]:
        if self.context is None or self.page is None:
            raise RuntimeError("browser_not_ready")
        auth = await self.login_state()
        if not auth.get("logged_in"):
            raise RuntimeError("login_not_confirmed")
        cookies = await self.context.cookies()
        try:
            user_agent = await self.page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = ""
        state = await self.context.storage_state()
        payload = {
            "cookies": state.get("cookies") or cookies,
            "origins": state.get("origins") or [],
            "user_agent": str(user_agent or ""),
            "locale": "de-DE",
            "captured_by": "vinted-session-worker",
            "authenticated_user_id": str(auth.get("user_id") or ""),
            "metric_endpoint_template": "/api/v2/items/{item_id}/details?localize=true",
        }
        meta = await save_vinted_session(payload, admin_user_id=self.admin_user_id)
        await finish_session_ticket(self.token, status="saved")
        self.status = "saved"
        self.detail = "Сессия сохранена в PostgreSQL"
        return meta


CAPTURES: dict[str, Capture] = {}
CAPTURE_LOCK = asyncio.Lock()


async def _ticket(request: web.Request) -> tuple[str, dict[str, Any]]:
    token = str(request.headers.get("X-DT-Session-Token") or request.query.get("token") or request.match_info.get("token") or "").strip()
    ticket = await get_session_ticket(token)
    if not ticket:
        raise web.HTTPForbidden(text="Ссылка входа истекла или недействительна.")
    return token, ticket


async def _capture_for(request: web.Request, *, create: bool = True) -> Capture:
    token, ticket = await _ticket(request)
    capture = CAPTURES.get(token)
    if capture is not None:
        return capture
    if not create:
        raise web.HTTPNotFound(text="Capture not started")
    async with CAPTURE_LOCK:
        capture = CAPTURES.get(token)
        if capture is not None:
            return capture
        active = [x for x in CAPTURES.values() if time.monotonic() - x.created_mono < CAPTURE_TTL_SECONDS]
        if len(active) >= MAX_ACTIVE_CAPTURES:
            raise web.HTTPServiceUnavailable(text="Уже открыт другой Vinted Session. Закрой его или подожди несколько минут.")
        capture = Capture(token=token, admin_user_id=int(ticket.get("admin_user_id") or 0))
        CAPTURES[token] = capture
    try:
        await capture.start()
    except Exception as exc:
        capture.status = "error"
        capture.detail = f"{type(exc).__name__}: {exc}"[:220]
        log.warning("Vinted session browser start failed: %s", exc)
    return capture


async def setup_page(request: web.Request) -> web.Response:
    # The one-time token lives in the URL fragment (#token=...), which browsers do
    # not send to Railway/proxy access logs. The page shell is harmless without it.
    body = """<!doctype html>
<html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1,maximum-scale=1\">
<title>DT Vinted Session</title>
<style>
body{margin:0;background:#111;color:#eee;font:15px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif} .top{position:sticky;top:0;z-index:5;background:#181818;padding:10px 12px;border-bottom:1px solid #333}
.title{font-size:18px;font-weight:700} .hint{opacity:.78;margin-top:4px} .wrap{max-width:1280px;margin:0 auto;padding:8px}
#screen{width:100%;height:auto;display:block;background:#222;border-radius:10px;touch-action:manipulation;cursor:crosshair} .bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
button{font-size:15px;padding:10px 14px;border-radius:9px;border:0;background:#2c2c2c;color:#fff} button.primary{background:#0b7a49} button:disabled{opacity:.45}
#status{padding:7px 0;min-height:22px} #kbd{position:fixed;left:-1000px;top:-1000px;opacity:.01;width:1px;height:1px}
.ok{color:#59d98e} .warn{color:#ffcb66}
</style></head><body>
<div class=\"top\"><div class=\"title\">🔐 DT · Vinted Session</div><div class=\"hint\">Кликни по полю на Vinted и просто печатай. Пароль бот не сохраняет и не показывает.</div><div id=\"status\">Запускаю браузер…</div></div>
<div class=\"wrap\"><img id=\"screen\" alt=\"Vinted browser\"><input id=\"kbd\" autocomplete=\"off\" autocapitalize=\"off\">
<div class=\"bar\"><button onclick=\"sendKey('Tab')\">Tab</button><button onclick=\"sendKey('Enter')\">Enter</button><button onclick=\"sendKey('Backspace')\">⌫</button><button onclick=\"reloadPage()\">↻ Обновить Vinted</button><button class=\"primary\" id=\"save\" onclick=\"saveSession()\" disabled>✅ Сохранить сессию</button></div></div>
<script>
const token = new URLSearchParams(location.hash.slice(1)).get('token') || new URLSearchParams(location.search).get('token') || '';
const screen = document.getElementById('screen');
const kbd = document.getElementById('kbd');
const statusEl = document.getElementById('status');
const saveBtn = document.getElementById('save');
let lastObjectUrl = null;
async function api(path, body) {
  const headers = {'x-dt-session-token': token};
  if (body) headers['content-type'] = 'application/json';
  const r = await fetch(path, {
    method: body ? 'POST' : 'GET', headers,
    body: body ? JSON.stringify(body) : undefined
  });
  if (!r.ok) throw new Error(await r.text());
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.blob();
}
async function refreshScreen() {
  if (!token) return;
  try {
    const r = await fetch('/api/screen?t=' + Date.now(), {headers: {'x-dt-session-token': token}});
    if (r.ok) {
      const nextUrl = URL.createObjectURL(await r.blob());
      screen.src = nextUrl;
      if (lastObjectUrl) URL.revokeObjectURL(lastObjectUrl);
      lastObjectUrl = nextUrl;
    }
  } catch (e) {}
}
async function refreshStatus() {
  if (!token) {
    statusEl.textContent = 'Ссылка входа недействительна: нет одноразового token.';
    return;
  }
  try {
    const s = await api('/api/status');
    saveBtn.disabled = !s.logged_in;
    statusEl.innerHTML = s.logged_in
      ? '<span class="ok">✅ Вход обнаружен. Нажми «Сохранить сессию».</span>'
      : '<span class="warn">🟡 ' + (s.detail || 'Войди в Vinted') + '</span>';
  } catch (e) {
    statusEl.textContent = String(e);
  }
}
screen.addEventListener('click', async e => {
  const r = screen.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width;
  const y = (e.clientY - r.top) / r.height;
  try {
    await api('/api/click', {x, y});
    kbd.focus({preventScroll: true});
  } catch (err) {
    statusEl.textContent = err;
  }
});
screen.addEventListener('touchend', () => kbd.focus({preventScroll: true}));
kbd.addEventListener('input', async () => {
  const text = kbd.value;
  kbd.value = '';
  if (text) {
    try { await api('/api/text', {text}); } catch (e) {}
  }
});
kbd.addEventListener('keydown', async e => {
  if (['Enter','Tab','Backspace','Escape','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)) {
    e.preventDefault();
    await sendKey(e.key);
  }
});
async function sendKey(key) {
  try {
    await api('/api/key', {key});
    kbd.focus({preventScroll: true});
  } catch (e) {}
}
async function reloadPage() {
  try { await api('/api/reload', {}); } catch (e) {}
}
async function saveSession() {
  saveBtn.disabled = true;
  statusEl.textContent = 'Сохраняю…';
  try {
    await api('/api/save', {});
    statusEl.innerHTML = '<span class="ok">✅ Сессия сохранена. Можно закрыть эту страницу и вернуться в Telegram.</span>';
  } catch (e) {
    statusEl.textContent = 'Не удалось сохранить: ' + e;
    saveBtn.disabled = false;
  }
}
setInterval(refreshScreen, 700);
setInterval(refreshStatus, 1800);
refreshScreen();
refreshStatus();
</script></body></html>"""
    return web.Response(text=body, content_type="text/html")


async def api_screen(request: web.Request) -> web.Response:
    capture = await _capture_for(request)
    if capture.page is None:
        raise web.HTTPServiceUnavailable(text=capture.detail or "Browser unavailable")
    try:
        data = await capture.page.screenshot(type="jpeg", quality=72)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except Exception as exc:
        raise web.HTTPServiceUnavailable(text=str(exc))


async def api_status(request: web.Request) -> web.Response:
    capture = await _capture_for(request)
    return web.json_response(await capture.login_state())


async def _json(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


async def api_click(request: web.Request) -> web.Response:
    capture = await _capture_for(request)
    data = await _json(request)
    if capture.page is None:
        raise web.HTTPServiceUnavailable()
    x = min(1.0, max(0.0, float(data.get("x") or 0.0))) * VIEWPORT_W
    y = min(1.0, max(0.0, float(data.get("y") or 0.0))) * VIEWPORT_H
    await capture.page.mouse.click(x, y)
    return web.json_response({"ok": True})


async def api_text(request: web.Request) -> web.Response:
    capture = await _capture_for(request)
    data = await _json(request)
    if capture.page is None:
        raise web.HTTPServiceUnavailable()
    text = str(data.get("text") or "")[:200]
    if text:
        # Never log text: it may be a password or 2FA code.
        await capture.page.keyboard.insert_text(text)
    return web.json_response({"ok": True})


async def api_key(request: web.Request) -> web.Response:
    capture = await _capture_for(request)
    data = await _json(request)
    if capture.page is None:
        raise web.HTTPServiceUnavailable()
    allowed = {"Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"}
    key = str(data.get("key") or "")
    if key not in allowed:
        raise web.HTTPBadRequest(text="unsupported key")
    await capture.page.keyboard.press(key)
    return web.json_response({"ok": True})


async def api_reload(request: web.Request) -> web.Response:
    capture = await _capture_for(request)
    if capture.page is None:
        raise web.HTTPServiceUnavailable()
    await capture.page.reload(wait_until="domcontentloaded", timeout=35_000)
    return web.json_response({"ok": True})


async def api_save(request: web.Request) -> web.Response:
    capture = await _capture_for(request)
    try:
        meta = await capture.save()
    except RuntimeError as exc:
        if str(exc) == "login_not_confirmed":
            raise web.HTTPConflict(text="Vinted login ещё не подтверждён: access_token_web/refresh_token_web не найден.")
        raise web.HTTPServiceUnavailable(text=str(exc))
    asyncio.create_task(capture.close())
    return web.json_response({"ok": True, "meta": meta})


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "version": APP_VERSION, "active": len(CAPTURES)})


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(30)
        now = time.monotonic()
        stale = [token for token, capture in CAPTURES.items() if now - capture.created_mono >= CAPTURE_TTL_SECONDS or capture.status == "saved"]
        for token in stale:
            capture = CAPTURES.pop(token, None)
            if capture is not None:
                try:
                    await capture.close()
                except Exception:
                    pass
                if capture.status != "saved":
                    try:
                        await finish_session_ticket(token, status="expired")
                    except Exception:
                        pass


async def service_heartbeat_loop(public_url: str) -> None:
    while True:
        try:
            await publish_session_service(public_url=public_url, version=APP_VERSION, status="online")
        except Exception as exc:
            log.warning("Vinted Session heartbeat failed: %s", exc)
        await asyncio.sleep(10)


async def main() -> None:
    if not os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError("Vinted Session Worker requires DATABASE_URL")
    await init_db()
    public_url = _public_url()
    port = int(os.getenv("PORT", "8080") or 8080)
    app = web.Application(client_max_size=64 * 1024)
    app.add_routes([
        web.get("/", health),
        web.get("/health", health),
        web.get("/setup", setup_page),
        web.get("/api/screen", api_screen),
        web.get("/api/status", api_status),
        web.post("/api/click", api_click),
        web.post("/api/text", api_text),
        web.post("/api/key", api_key),
        web.post("/api/reload", api_reload),
        web.post("/api/save", api_save),
    ])
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("DT Vinted Session Worker online | version=%s port=%s public_url=%s", APP_VERSION, port, public_url or "missing")
    if not public_url:
        log.warning("Generate a Railway public domain for Vinted Session Worker; admin panel will stay unavailable until then")
    tasks = [asyncio.create_task(cleanup_loop()), asyncio.create_task(service_heartbeat_loop(public_url))]
    try:
        await asyncio.Event().wait()
    finally:
        for task in tasks:
            task.cancel()
        for capture in list(CAPTURES.values()):
            await capture.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
