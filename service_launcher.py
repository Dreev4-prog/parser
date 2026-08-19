from __future__ import annotations

import os
import sys


def _normalized(value: str) -> str:
    return "".join(ch for ch in (value or "").strip().lower() if ch.isalnum())


def _role() -> str:
    # Optional explicit override for future deployments. It is NOT required on Railway.
    explicit = _normalized(os.getenv("DT_SERVICE_ROLE", ""))
    if explicit in {"viewworker", "viewcounterworker", "viewsworker"}:
        return "view-worker"
    if explicit in {"bot", "main", "parserbot", "telegrambot"}:
        return "bot"

    # Railway provides RAILWAY_SERVICE_NAME automatically.
    service_name = os.getenv("RAILWAY_SERVICE_NAME", "")
    normalized = _normalized(service_name)

    # The dedicated service created for v4.3.14/15 is named "View Worker".
    # Keep matching intentionally narrow so unrelated worker services are not hijacked.
    if normalized in {"viewworker", "viewcounterworker"}:
        return "view-worker"

    return "bot"


def main() -> None:
    role = _role()
    service_name = os.getenv("RAILWAY_SERVICE_NAME", "local")
    target = "view_counter_worker.py" if role == "view-worker" else "bot.py"

    print(
        f"[service-launcher] service={service_name!r} role={role} target={target}",
        flush=True,
    )

    # Replace the launcher process so Railway signals/restarts target the real process.
    os.execv(sys.executable, [sys.executable, target])


if __name__ == "__main__":
    main()
