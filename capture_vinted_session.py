#!/usr/bin/env python3
"""Capture a normal Vinted browser session for DT Vinted Metrics Worker.

This helper is intentionally manual and conservative:
- the user logs in themselves in a visible browser;
- passwords/2FA are never read by this script;
- no CAPTCHA solving, stealth plugins, proxy rotation, or anti-bot bypass is used;
- the resulting session file is a SECRET and should only be stored in Railway.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except Exception:
    print("Playwright не установлен. Запусти: python3 -m pip install playwright && python3 -m playwright install chromium")
    raise

BASE_URL = "https://www.vinted.de"
DEFAULT_ENDPOINT = "/api/v2/items/{item_id}/details?localize=true"
ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "vinted_session.json"
OUT_RAILWAY = ROOT / "VINTED_SESSION_RAILWAY.txt"


def _extract_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item = raw.get("item") if isinstance(raw.get("item"), dict) else raw
    if not isinstance(item, dict):
        return None
    try:
        item_id = int(item.get("id"))
    except Exception:
        return None
    return {
        "id": item_id,
        "view_count": item.get("view_count", item.get("views_count", item.get("views"))),
        "favourite_count": item.get("favourite_count", item.get("favorites_count", item.get("favourites_count"))),
        "upload": item.get("created_at", item.get("created_at_ts", item.get("upload_date", item.get("uploaded_at")))),
    }


def _fetch_json(page, path: str) -> dict[str, Any]:
    return page.evaluate(
        """
        async ({path}) => {
          try {
            const r = await fetch(path, {
              credentials: 'include',
              headers: {'Accept':'application/json, text/plain, */*','X-Requested-With':'XMLHttpRequest'}
            });
            const text = await r.text();
            let data = null;
            try { data = JSON.parse(text); } catch (_) {}
            return {status:r.status, data, preview:text.slice(0,1200), url:r.url};
          } catch (e) {
            return {status:0, data:null, preview:String(e), url:path};
          }
        }
        """,
        {"path": path},
    )


def _template_from_url(url: str, item_id: int) -> str | None:
    if not url or str(item_id) not in url:
        return None
    value = url
    if value.startswith(BASE_URL):
        value = value[len(BASE_URL):]
    value = value.replace(str(item_id), "{item_id}", 1)
    if not value.startswith("/"):
        value = "/" + value
    return value


def _safe_cookie(cookie: dict[str, Any]) -> dict[str, Any] | None:
    domain = str(cookie.get("domain") or "").lstrip(".").lower()
    if not domain.endswith("vinted.de"):
        return None
    keep = {
        "name": str(cookie.get("name") or ""),
        "value": str(cookie.get("value") or ""),
        "domain": str(cookie.get("domain") or "www.vinted.de"),
        "path": str(cookie.get("path") or "/"),
        "expires": cookie.get("expires"),
        "httpOnly": bool(cookie.get("httpOnly", False)),
        "secure": bool(cookie.get("secure", True)),
        "sameSite": str(cookie.get("sameSite") or "Lax"),
    }
    return keep if keep["name"] else None


def main() -> int:
    print("\nDT Vinted Session Capture")
    print("Пароль/2FA вводишь только в окне Vinted. Скрипт их не читает и не сохраняет.\n")

    with sync_playwright() as p:
        browser = None
        try:
            try:
                browser = p.chromium.launch(channel="chrome", headless=False)
                print("Открыт установленный Google Chrome.")
            except Exception:
                browser = p.chromium.launch(headless=False)
                print("Открыт Chromium от Playwright.")
        except PlaywrightError as exc:
            print(f"Не удалось открыть браузер: {exc}")
            print("Если Chromium не установлен: python3 -m playwright install chromium")
            return 2

        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        print("\n1) В открытом браузере войди в СВОЙ аккаунт Vinted обычным способом.")
        print("2) После полного входа вернись в это окно Terminal и нажми Enter.")
        input("\nНажми Enter после входа: ")

        page.goto(f"{BASE_URL}/catalog", wait_until="domcontentloaded", timeout=45_000)
        time.sleep(1.0)
        user_agent = page.evaluate("navigator.userAgent")

        catalog = _fetch_json(page, "/api/v2/catalog/items?page=1&per_page=1&order=newest_first")
        if int(catalog.get("status") or 0) != 200 or not isinstance(catalog.get("data"), dict):
            print(f"\n❌ Catalog test HTTP {catalog.get('status')}. Сессию сохранять как рабочую нельзя.")
            browser.close()
            return 3
        items = catalog["data"].get("items")
        if not isinstance(items, list) or not items:
            print("\n❌ Vinted catalog не вернул тестовое объявление.")
            browser.close()
            return 3
        first = items[0]
        try:
            item_id = int(first.get("id"))
        except Exception:
            print("\n❌ Не удалось определить ID тестового объявления.")
            browser.close()
            return 3
        item_url = str(first.get("url") or f"{BASE_URL}/items/{item_id}")
        if item_url.startswith("/"):
            item_url = BASE_URL + item_url

        endpoint_template = DEFAULT_ENDPOINT
        exact_test = _fetch_json(page, DEFAULT_ENDPOINT.format(item_id=item_id))
        exact_payload = _extract_item(exact_test.get("data"))

        discovered: list[tuple[str, dict[str, Any]]] = []
        if int(exact_test.get("status") or 0) != 200 or not exact_payload or exact_payload.get("view_count") is None:
            print(f"\nDefault details: HTTP {exact_test.get('status')} / views={None if not exact_payload else exact_payload.get('view_count')}.")
            print("Открою ОДНО тестовое объявление и посмотрю обычные сетевые ответы Vinted.")
            print("Это объявление используется только для настройки и не является Radar-замером.")

            def on_response(response):
                try:
                    if response.status != 200 or "/api/" not in response.url:
                        return
                    if str(item_id) not in response.url:
                        return
                    ctype = (response.headers.get("content-type") or "").lower()
                    if "json" not in ctype:
                        return
                    payload = response.json()
                    parsed = _extract_item(payload)
                    if parsed and parsed.get("id") == item_id and parsed.get("view_count") is not None:
                        discovered.append((response.url, parsed))
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                page.goto(item_url, wait_until="domcontentloaded", timeout=45_000)
                time.sleep(5.0)
            finally:
                page.remove_listener("response", on_response)

            if discovered:
                endpoint_template = _template_from_url(discovered[0][0], item_id) or DEFAULT_ENDPOINT
                exact_payload = discovered[0][1]
                print(f"✅ Найден exact JSON endpoint: {endpoint_template}")
                # Return to catalog so the production-equivalent test never needs item navigation.
                page.goto(f"{BASE_URL}/catalog", wait_until="domcontentloaded", timeout=45_000)
                time.sleep(0.5)
                retest = _fetch_json(page, endpoint_template.format(item_id=item_id))
                parsed = _extract_item(retest.get("data"))
                if int(retest.get("status") or 0) == 200 and parsed and parsed.get("view_count") is not None:
                    exact_payload = parsed
                    exact_test = retest
                else:
                    print(f"❌ Найденный endpoint нельзя повторно прочитать из catalog context: HTTP {retest.get('status')}")
                    exact_payload = None
            else:
                print("❌ В обычных JSON-ответах карточки endpoint с view_count не найден.")
                print("Сессию сохраню, но Exact Views gate останется закрыт до следующего адаптера.")

        storage = context.storage_state()
        cookies = [safe for raw in list(storage.get("cookies") or []) if (safe := _safe_cookie(raw))]
        cookie_names = sorted({str(c.get("name") or "") for c in cookies})
        payload = {
            "schema": "dt-vinted-session-v2",
            "base_url": BASE_URL,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "locale": "de-DE",
            "user_agent": str(user_agent or ""),
            "metric_endpoint_template": endpoint_template,
            "setup_probe_item_id": item_id,
            "exact_probe_pass": bool(exact_payload and exact_payload.get("view_count") is not None),
            "cookies": cookies,
        }
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        OUT_RAILWAY.write_text("VINTED_SESSION_JSON=" + compact, encoding="utf-8")

        print("\n--- Результат ---")
        print(f"Cookies: {', '.join(cookie_names)}")
        print(f"access_token_web: {'есть' if 'access_token_web' in cookie_names else 'НЕТ'}")
        print(f"refresh_token_web: {'есть' if 'refresh_token_web' in cookie_names else 'НЕТ'}")
        if exact_payload:
            print(f"Exact test: PASS · views={exact_payload.get('view_count')} · favourites={exact_payload.get('favourite_count')} · chronology={exact_payload.get('upload')}")
        else:
            print("Exact test: FAIL · exact views пока не подтверждены")
        print(f"\nСохранено: {OUT_JSON.name}")
        print(f"Для Railway: {OUT_RAILWAY.name}")
        print("⚠️ Эти два файла содержат секретную Vinted-сессию. Не отправляй их в чат и не коммить в Git.")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
