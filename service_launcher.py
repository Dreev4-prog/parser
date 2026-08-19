from __future__ import annotations

import os
import sys


def _normalized(value: str) -> str:
    return "".join(ch for ch in (value or "").strip().lower() if ch.isalnum())


def _role() -> str:
    # Optional explicit override. Useful if another worker service is added later.
    explicit = _normalized(os.getenv("DT_SERVICE_ROLE", ""))
    if explicit in {"viewworker", "viewcounterworker", "viewsworker"}:
        return "view-worker"
    if explicit in {"bot", "main", "parserbot", "telegrambot"}:
        return "bot"

    # Railway normally provides the service name. Match both the exact friendly
    # name and any future variant that clearly contains VIEW + WORKER.
    service_name = os.getenv("RAILWAY_SERVICE_NAME", "")
    normalized = _normalized(service_name)
    if normalized in {"viewworker", "viewcounterworker"} or (
        "view" in normalized and "worker" in normalized
    ):
        return "view-worker"

    # v4.3.17 safety net for Railway services created from the same repo:
    # the dedicated View Worker intentionally needs Redis only, while the main
    # Telegram bot always has BOT_TOKEN + DATABASE_URL. Railway may temporarily
    # expose the service's generated/original name even after a UI rename, so do
    # not depend on the display name alone.
    has_redis = bool(os.getenv("REDIS_URL", "").strip())
    has_database = bool(os.getenv("DATABASE_URL", "").strip())
    has_bot_token = bool(os.getenv("BOT_TOKEN", "").strip())
    if has_redis and not has_database and not has_bot_token:
        return "view-worker"

    return "bot"


def main() -> None:
    role = _role()
    service_name = os.getenv("RAILWAY_SERVICE_NAME", "local")
    target = "view_counter_worker.py" if role == "view-worker" else "bot.py"

    print(
        "[service-launcher] "
        f"service={service_name!r} role={role} target={target} "
        f"redis={'yes' if os.getenv('REDIS_URL', '').strip() else 'no'} "
        f"database={'yes' if os.getenv('DATABASE_URL', '').strip() else 'no'} "
        f"bot_token={'yes' if os.getenv('BOT_TOKEN', '').strip() else 'no'}",
        flush=True,
    )

    # Replace the launcher process so Railway signals/restarts target the real process.
    os.execv(sys.executable, [sys.executable, target])


if __name__ == "__main__":
    main()
