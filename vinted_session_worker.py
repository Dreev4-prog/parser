from __future__ import annotations

import asyncio
import io
import logging
import os
import zipfile
from pathlib import Path
from typing import Any

from aiohttp import web

from app_version import APP_VERSION
from db import init_db
from vinted_local_session import MAX_SESSION_BODY, sanitize_local_session
from vinted_session_store import (
    finish_session_ticket,
    get_session_ticket,
    publish_session_service,
    save_vinted_session,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("vinted-session-worker")

HELPER_DIR = Path(__file__).resolve().parent / "vinted_local_helper"


def _public_url() -> str:
    explicit = (os.getenv("VINTED_SESSION_PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    for key in ("RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL"):
        value = (os.getenv(key) or "").strip().rstrip("/")
        if value:
            return value if value.startswith("http://") or value.startswith("https://") else f"https://{value}"
    return ""


async def _json(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


async def _ticket_from_request(request: web.Request, body: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    body = body or {}
    token = str(
        request.headers.get("X-DT-Session-Token")
        or body.get("token")
        or request.query.get("token")
        or request.match_info.get("token")
        or ""
    ).strip()
    ticket = await get_session_ticket(token)
    if not ticket:
        raise web.HTTPForbidden(text="Ссылка входа истекла или недействительна.")
    return token, ticket


async def setup_page(request: web.Request) -> web.Response:
    body = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DT Vinted Local Session</title>
<style>
body{margin:0;background:#101214;color:#f1f3f5;font:15px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}.wrap{max-width:760px;margin:0 auto;padding:22px}.card{background:#191c20;border:1px solid #2b3036;border-radius:16px;padding:18px;margin:12px 0}.title{font-size:22px;font-weight:800}.muted{opacity:.75}.ok{color:#68df9b}.warn{color:#ffd166}.bad{color:#ff7b7b}.step{font-size:17px;font-weight:700;margin:5px 0 8px}button,a.btn{display:inline-block;background:#2d333b;color:#fff;border:0;border-radius:10px;padding:12px 15px;text-decoration:none;font-weight:700;margin:5px 6px 5px 0}.primary{background:#0b7a49!important}.small{font-size:13px;opacity:.72;line-height:1.45}code{background:#0e1012;border-radius:5px;padding:2px 5px}
</style></head><body><div class="wrap">
<div class="title">🔐 DT · Vinted Session</div>
<p class="muted">Вход теперь выполняется <b>в твоём обычном Chrome и с твоего интернет-соединения</b>. Railway больше не открывает страницу входа Vinted.</p>
<div class="card"><div class="step">1. Local Helper — один раз</div>
<p>Если Helper ещё не установлен: скачай архив, распакуй его, затем в Chrome открой <code>chrome://extensions</code> → включи <b>Режим разработчика</b> → <b>Загрузить распакованное расширение</b> → выбери папку <code>vinted_local_helper</code>.</p>
<a class="btn" href="/helper.zip">⬇️ Скачать DT Vinted Local Helper</a>
<div class="small">Расширение имеет доступ только к cookies домена Vinted. Оно ничего не делает без одноразовой ссылки из этой админ-панели и не сохраняет cookies локально.</div></div>
<div class="card"><div class="step">2. Войти в Vinted</div>
<p id="ticket">Проверяю одноразовую ссылку…</p>
<button id="open" class="primary" disabled>🌐 Открыть Vinted в моём Chrome</button>
<p class="small">После входа Helper проверит <code>/api/v2/users/current</code>. Когда Vinted подтвердит твой аккаунт, сессия автоматически вернётся в DT Session Worker по HTTPS. Пароль и 2FA-код DT не читает и не получает.</p></div>
<div class="card"><div class="step">3. Готово</div><p>После успешного входа откроется страница <b>✅ Сессия сохранена</b>. Затем просто вернись в Telegram и нажми <b>🔄 Проверить</b>.</p></div>
</div>
<script>
const token=(new URLSearchParams(location.hash.slice(1))).get('token')||'';
const ticket=document.getElementById('ticket'); const openBtn=document.getElementById('open');
function b64url(s){return btoa(unescape(encodeURIComponent(s))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
async function check(){
 if(!token){ticket.innerHTML='<span class="bad">❌ Одноразовый token отсутствует. Вернись в Telegram и создай новую ссылку.</span>'; return;}
 try{const r=await fetch('/api/ticket',{method:'POST',headers:{'X-DT-Session-Token':token}}); if(!r.ok) throw new Error(await r.text()); ticket.innerHTML='<span class="ok">✅ Ссылка активна 15 минут.</span>'; openBtn.disabled=false;}
 catch(e){ticket.innerHTML='<span class="bad">❌ Ссылка истекла или недействительна. Создай новую в Telegram.</span>';}
}
openBtn.onclick=()=>{
 const pair={u:location.origin,t:token,ts:Date.now()};
 const target='https://www.vinted.de/member/general/login#dtv='+b64url(JSON.stringify(pair));
 location.href=target;
};
check();
</script></body></html>'''
    return web.Response(text=body, content_type="text/html", headers={"Cache-Control": "no-store"})


async def api_ticket(request: web.Request) -> web.Response:
    _, ticket = await _ticket_from_request(request)
    return web.json_response({"ok": True, "expires_at": str(ticket.get("expires_at") or "")})


async def receive_page(request: web.Request) -> web.Response:
    body = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DT Vinted Session</title>
<style>body{background:#101214;color:#f4f4f4;font:16px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:0}.wrap{max-width:680px;margin:60px auto;padding:24px}.card{background:#191c20;border:1px solid #2b3036;border-radius:16px;padding:22px}.ok{color:#68df9b}.bad{color:#ff7b7b}.muted{opacity:.72}</style></head><body><div class="wrap"><div class="card"><h2>🔐 DT · Vinted Session</h2><p id="status">Сохраняю локальную Vinted-сессию…</p><p class="muted">Эту страницу можно закрыть после завершения.</p></div></div>
<script>
const status=document.getElementById('status'); const raw=(new URLSearchParams(location.hash.slice(1))).get('payload')||'';
function decode64(s){s=s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4)s+='='; return decodeURIComponent(escape(atob(s)));}
(async()=>{try{if(!raw)throw new Error('payload_missing'); const env=JSON.parse(decode64(raw)); history.replaceState(null,'',location.pathname); const r=await fetch('/api/local/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(env)}); const text=await r.text(); if(!r.ok)throw new Error(text||('HTTP '+r.status)); status.innerHTML='<span class="ok">✅ Сессия сохранена. Вернись в Telegram → Vinted Session → 🔄 Проверить.</span>'; }catch(e){status.innerHTML='<span class="bad">❌ Не удалось сохранить сессию: '+String(e).replace(/[<>]/g,'')+'</span>';}})();
</script></body></html>'''
    return web.Response(text=body, content_type="text/html", headers={"Cache-Control": "no-store"})


async def api_local_import(request: web.Request) -> web.Response:
    body = await _json(request)
    token, ticket = await _ticket_from_request(request, body)
    try:
        session_payload = sanitize_local_session(body.get("session"))
    except ValueError as exc:
        reason = str(exc)
        if reason == "login_not_confirmed":
            raise web.HTTPConflict(text="Vinted login не подтверждён локальным браузером.")
        raise web.HTTPBadRequest(text=f"Некорректная локальная Vinted-сессия: {reason}")
    meta = await save_vinted_session(
        session_payload,
        admin_user_id=int(ticket.get("admin_user_id") or 0),
        source="admin-local-browser-helper",
    )
    await finish_session_ticket(token, status="saved")
    log.info(
        "Vinted local session saved | admin=%s cookies=%s fingerprint=%s",
        int(ticket.get("admin_user_id") or 0),
        int(meta.get("cookie_count", 0) or 0),
        str(meta.get("fingerprint") or ""),
    )
    return web.json_response({"ok": True, "meta": meta})


async def helper_zip(request: web.Request) -> web.Response:
    if not HELPER_DIR.is_dir():
        raise web.HTTPNotFound(text="Local Helper files are missing in this deployment.")
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(HELPER_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(Path("vinted_local_helper") / path.relative_to(HELPER_DIR)))
    payload = memory.getvalue()
    return web.Response(
        body=payload,
        content_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="DT_VINTED_LOCAL_HELPER.zip"',
            "Cache-Control": "no-store",
        },
    )


async def health(request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "version": APP_VERSION,
        "mode": "local-browser-helper",
        "helper_available": HELPER_DIR.is_dir(),
    })


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
    app = web.Application(client_max_size=MAX_SESSION_BODY + 16 * 1024)
    app.add_routes([
        web.get("/", health),
        web.get("/health", health),
        web.get("/setup", setup_page),
        web.post("/api/ticket", api_ticket),
        web.get("/local/receive", receive_page),
        web.post("/api/local/import", api_local_import),
        web.get("/helper.zip", helper_zip),
    ])
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(
        "DT Vinted Session Worker online | version=%s mode=local-browser-helper port=%s public_url=%s",
        APP_VERSION,
        port,
        public_url or "missing",
    )
    if not public_url:
        log.warning("Generate a Railway public domain for Vinted Session Worker; admin panel will stay unavailable until then")
    task = asyncio.create_task(service_heartbeat_loop(public_url))
    try:
        await asyncio.Event().wait()
    finally:
        task.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
