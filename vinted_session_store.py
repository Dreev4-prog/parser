from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from db import SessionLocal
from models import AppSetting

SESSION_KEY = "vinted_session_json"
SESSION_META_KEY = "vinted_session_meta"
SESSION_SERVICE_KEY = "vinted_session_service"
TICKET_PREFIX = "vinted_session_ticket_"


def utcnow() -> datetime:
    return datetime.utcnow()


async def _get(key: str) -> tuple[str, datetime | None]:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, key)
        if row is None:
            return "", None
        return str(row.value or ""), row.updated_at


async def _set(key: str, value: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value, updated_at=utcnow())
            session.add(row)
        else:
            row.value = value
            row.updated_at = utcnow()
        await session.commit()


async def _delete(key: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, key)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def load_vinted_session_json() -> str:
    value, _ = await _get(SESSION_KEY)
    return value.strip()


async def load_vinted_session_meta() -> dict[str, Any]:
    value, updated_at = await _get(SESSION_META_KEY)
    try:
        payload = json.loads(value) if value else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if updated_at is not None:
        payload.setdefault("updated_at", updated_at.isoformat())
    return payload


async def save_vinted_session(session_payload: dict[str, Any], *, admin_user_id: int, source: str = "admin-remote-browser") -> dict[str, Any]:
    raw = json.dumps(session_payload, ensure_ascii=False, separators=(",", ":"))
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    cookies = session_payload.get("cookies") if isinstance(session_payload, dict) else None
    cookie_names: list[str] = []
    if isinstance(cookies, list):
        cookie_names = sorted({str(x.get("name") or "") for x in cookies if isinstance(x, dict) and x.get("name")})
    elif isinstance(cookies, dict):
        cookie_names = sorted(str(x) for x in cookies.keys())
    meta = {
        "captured_at": utcnow().isoformat(),
        "admin_user_id": int(admin_user_id),
        "source": source,
        "fingerprint": fingerprint,
        "cookie_count": len(cookie_names),
        "has_access_token_web": "access_token_web" in cookie_names,
        "has_refresh_token_web": "refresh_token_web" in cookie_names,
    }
    await _set(SESSION_KEY, raw)
    await _set(SESSION_META_KEY, json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    return meta


async def clear_vinted_session() -> None:
    await _delete(SESSION_KEY)
    await _delete(SESSION_META_KEY)


async def session_fingerprint() -> str:
    meta = await load_vinted_session_meta()
    return str(meta.get("fingerprint") or "")


async def create_session_ticket(admin_user_id: int, *, ttl_minutes: int = 15) -> str:
    token = secrets.token_urlsafe(18)
    expires = utcnow() + timedelta(minutes=max(3, min(60, int(ttl_minutes))))
    payload = {
        "admin_user_id": int(admin_user_id),
        "created_at": utcnow().isoformat(),
        "expires_at": expires.isoformat(),
        "status": "active",
    }
    await _set(TICKET_PREFIX + token, json.dumps(payload, separators=(",", ":")))
    return token


async def get_session_ticket(token: str) -> dict[str, Any] | None:
    token = (token or "").strip()
    if not token or len(token) > 64:
        return None
    value, _ = await _get(TICKET_PREFIX + token)
    if not value:
        return None
    try:
        payload = json.loads(value)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "active":
        return None
    try:
        expires_at = datetime.fromisoformat(str(payload.get("expires_at") or ""))
    except Exception:
        return None
    if utcnow() >= expires_at:
        await _delete(TICKET_PREFIX + token)
        return None
    return payload


async def finish_session_ticket(token: str, *, status: str = "saved") -> None:
    token = (token or "").strip()
    if not token:
        return
    value, _ = await _get(TICKET_PREFIX + token)
    if not value:
        return
    try:
        payload = json.loads(value)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["status"] = status
    payload["finished_at"] = utcnow().isoformat()
    await _set(TICKET_PREFIX + token, json.dumps(payload, separators=(",", ":")))


async def publish_session_service(*, public_url: str, version: str, status: str = "online") -> None:
    payload = {
        "public_url": public_url.rstrip("/"),
        "version": str(version),
        "status": status,
        "heartbeat_at": utcnow().isoformat(),
    }
    await _set(SESSION_SERVICE_KEY, json.dumps(payload, separators=(",", ":")))


async def get_session_service() -> dict[str, Any]:
    value, updated_at = await _get(SESSION_SERVICE_KEY)
    try:
        payload = json.loads(value) if value else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return {}
    if updated_at is not None:
        age = max(0.0, (utcnow() - updated_at).total_seconds())
        payload["heartbeat_age_seconds"] = age
        payload["online"] = age <= 30.0 and str(payload.get("status") or "") == "online"
    else:
        payload["online"] = False
    return payload
