from __future__ import annotations

import json
from typing import Any

MAX_SESSION_BODY = 128 * 1024
MAX_COOKIES = 180

def _safe_cookie(cookie: dict[str, Any]) -> dict[str, Any] | None:
    name = str(cookie.get("name") or "").strip()
    value = cookie.get("value")
    domain = str(cookie.get("domain") or "").strip().lower()
    if not name or value is None or len(name) > 256 or len(str(value)) > 16_384:
        return None
    host = domain.lstrip(".")
    if host != "vinted.de" and not host.endswith(".vinted.de"):
        return None
    out: dict[str, Any] = {
        "name": name,
        "value": str(value),
        "domain": domain or ".vinted.de",
        "path": str(cookie.get("path") or "/")[:1024] or "/",
        "httpOnly": bool(cookie.get("httpOnly", False)),
        "secure": bool(cookie.get("secure", True)),
    }
    expires = cookie.get("expires")
    try:
        if expires is not None and float(expires) > 0:
            out["expires"] = float(expires)
    except Exception:
        pass
    same_site = str(cookie.get("sameSite") or "").strip().lower().replace("_", "")
    if same_site in {"strict", "lax", "none", "norestriction"}:
        out["sameSite"] = "None" if same_site in {"none", "norestriction"} else same_site.capitalize()
    return out


def sanitize_local_session(payload: Any) -> dict[str, Any]:
    """Accept only a bounded first-party Vinted browser session from the local helper."""
    if not isinstance(payload, dict):
        raise ValueError("session_not_object")
    raw_cookies = payload.get("cookies")
    if not isinstance(raw_cookies, list):
        raise ValueError("cookies_missing")
    if len(raw_cookies) > MAX_COOKIES:
        raise ValueError("too_many_cookies")
    cookies: list[dict[str, Any]] = []
    for raw in raw_cookies:
        if not isinstance(raw, dict):
            continue
        safe = _safe_cookie(raw)
        if safe is not None:
            cookies.append(safe)
    names = {str(x.get("name") or "") for x in cookies}
    # Anonymous Vinted bootstrap can also have token-shaped cookies.  The helper
    # therefore supplies the /api/v2/users/current identity it observed locally.
    user_id = str(payload.get("authenticated_user_id") or "").strip()
    if not user_id:
        raise ValueError("login_not_confirmed")
    if "access_token_web" not in names:
        raise ValueError("access_cookie_missing")
    user_agent = str(payload.get("user_agent") or "").strip()[:1024]
    locale = str(payload.get("locale") or "de-DE").strip()[:64] or "de-DE"
    result = {
        "cookies": cookies,
        "origins": [],
        "user_agent": user_agent,
        "locale": locale,
        "captured_by": "dt-vinted-local-helper",
        "authenticated_user_id": user_id[:128],
        "metric_endpoint_template": "/api/v2/items/{item_id}/details?localize=true",
    }
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_SESSION_BODY:
        raise ValueError("session_too_large")
    return result

