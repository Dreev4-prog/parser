from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import func, or_, select

from db import SessionLocal
from models import AppSetting, BotUser, SubscriptionPayment, SubscriptionPlan, UserScan

log = logging.getLogger("kleinanzeigen-commerce")

CRYPTO_PAY_TOKEN = (os.getenv("CRYPTO_PAY_TOKEN") or os.getenv("CRYPTOBOT_TOKEN") or "").strip()
XROCKET_API_KEY = (os.getenv("XROCKET_API_KEY") or os.getenv("XROCKET_TOKEN") or "").strip()
PAYMENT_POLL_SECONDS = max(10, int(os.getenv("PAYMENT_POLL_SECONDS", "25")))
PAYMENT_INVOICE_TTL_SECONDS = max(300, min(86400, int(os.getenv("PAYMENT_INVOICE_TTL_SECONDS", "3600"))))
ACCESS_MODE_DEFAULT = (os.getenv("ACCESS_MODE", "admin_only").strip().lower() or "admin_only")
if ACCESS_MODE_DEFAULT not in {"admin_only", "subscription", "open"}:
    ACCESS_MODE_DEFAULT = "admin_only"

# Initial plans only. Once created, prices/statuses are edited from the admin panel.
DEFAULT_PLAN_SPECS = (
    ("1d", "1 день", 1, float(os.getenv("PLAN_1D_USDT", "5")), 10),
    ("3d", "3 дня", 3, float(os.getenv("PLAN_3D_USDT", "10")), 20),
    ("7d", "7 дней", 7, float(os.getenv("PLAN_7D_USDT", "20")), 30),
    ("30d", "30 дней", 30, float(os.getenv("PLAN_30D_USDT", "50")), 40),
)

_access_mode = ACCESS_MODE_DEFAULT
_access_cache: dict[int, datetime] = {}
_banned_users: set[int] = set()
_activity_write_cache: dict[int, float] = {}
_activity_guard = asyncio.Lock()
_payment_locks: dict[int, asyncio.Lock] = {}
_payment_locks_guard = asyncio.Lock()


class PaymentProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class InvoiceCreated:
    provider: str
    external_id: str
    pay_url: str
    status: str = "active"


def current_access_mode() -> str:
    return _access_mode


def provider_enabled(provider: str) -> bool:
    if provider == "cryptobot":
        return bool(CRYPTO_PAY_TOKEN)
    if provider == "xrocket":
        return bool(XROCKET_API_KEY)
    return False


def providers_status() -> dict[str, bool]:
    return {"cryptobot": bool(CRYPTO_PAY_TOKEN), "xrocket": bool(XROCKET_API_KEY)}


def has_access(user_id: int, admin_ids: set[int]) -> bool:
    if user_id in admin_ids:
        return True
    if user_id in _banned_users:
        return False
    if _access_mode == "open":
        return True
    if _access_mode == "admin_only":
        return False
    until = _access_cache.get(user_id)
    return bool(until and until > datetime.utcnow())


def cached_access_until(user_id: int) -> datetime | None:
    return _access_cache.get(user_id)


def is_banned_cached(user_id: int) -> bool:
    return user_id in _banned_users


async def initialize_commerce() -> None:
    global _access_mode
    async with SessionLocal() as session:
        setting = await session.get(AppSetting, "access_mode")
        if setting is None:
            setting = AppSetting(key="access_mode", value=ACCESS_MODE_DEFAULT, updated_at=datetime.utcnow())
            session.add(setting)
        elif setting.value in {"admin_only", "subscription", "open"}:
            _access_mode = setting.value

        for key, title, days, price, order in DEFAULT_PLAN_SPECS:
            existing = await session.get(SubscriptionPlan, key)
            if existing is None:
                session.add(SubscriptionPlan(
                    key=key,
                    title=title,
                    days=days,
                    price_usdt=price,
                    is_active=True,
                    sort_order=order,
                    updated_at=datetime.utcnow(),
                ))

        # Preserve historical scanner users in statistics after upgrading from v3.1.x.
        # Username/name will be filled automatically on their next Telegram action.
        historical_ids = list((await session.execute(select(UserScan.user_id).distinct())).scalars().all())
        now = datetime.utcnow()
        for uid in historical_ids:
            if await session.get(BotUser, int(uid)) is None:
                session.add(BotUser(user_id=int(uid), joined_at=now, last_seen_at=now))
        await session.commit()

        users = (await session.execute(select(BotUser))).scalars().all()
        _access_cache.clear()
        _banned_users.clear()
        now = datetime.utcnow()
        for user in users:
            if user.is_banned:
                _banned_users.add(user.user_id)
            if user.access_until and user.access_until > now:
                _access_cache[user.user_id] = user.access_until


async def set_access_mode(mode: str) -> str:
    global _access_mode
    if mode not in {"admin_only", "subscription", "open"}:
        raise ValueError("invalid access mode")
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "access_mode")
        if row is None:
            row = AppSetting(key="access_mode", value=mode, updated_at=datetime.utcnow())
            session.add(row)
        else:
            row.value = mode
            row.updated_at = datetime.utcnow()
        await session.commit()
    _access_mode = mode
    return mode


async def touch_user(tg_user: Any, *, force: bool = False) -> BotUser | None:
    if tg_user is None:
        return None
    uid = int(tg_user.id)
    loop_time = asyncio.get_running_loop().time()
    async with _activity_guard:
        last = _activity_write_cache.get(uid, 0.0)
        if not force and loop_time - last < 60:
            return None
        _activity_write_cache[uid] = loop_time

    async with SessionLocal() as session:
        row = await session.get(BotUser, uid)
        now = datetime.utcnow()
        if row is None:
            row = BotUser(
                user_id=uid,
                username=getattr(tg_user, "username", None),
                first_name=getattr(tg_user, "first_name", None),
                joined_at=now,
                last_seen_at=now,
            )
            session.add(row)
        else:
            row.username = getattr(tg_user, "username", None)
            row.first_name = getattr(tg_user, "first_name", None)
            row.last_seen_at = now
        await session.commit()
        return row


async def get_user(user_id: int) -> BotUser | None:
    async with SessionLocal() as session:
        return await session.get(BotUser, int(user_id))


async def find_users(query: str, limit: int = 20) -> list[BotUser]:
    query = (query or "").strip().lstrip("@")
    async with SessionLocal() as session:
        stmt = select(BotUser)
        if query.isdigit():
            uid = int(query)
            stmt = stmt.where(or_(BotUser.user_id == uid, BotUser.username.ilike(f"%{query}%")))
        elif query:
            stmt = stmt.where(or_(BotUser.username.ilike(f"%{query}%"), BotUser.first_name.ilike(f"%{query}%")))
        stmt = stmt.order_by(BotUser.last_seen_at.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars().all())


async def recent_users(limit: int = 20) -> list[BotUser]:
    async with SessionLocal() as session:
        return list((await session.execute(
            select(BotUser).order_by(BotUser.last_seen_at.desc()).limit(limit)
        )).scalars().all())


async def grant_access_days(user_id: int, days: int) -> datetime:
    days = max(1, min(3650, int(days)))
    async with SessionLocal() as session:
        row = await session.get(BotUser, int(user_id))
        if row is None:
            row = BotUser(user_id=int(user_id), joined_at=datetime.utcnow(), last_seen_at=datetime.utcnow())
            session.add(row)
        now = datetime.utcnow()
        base = row.access_until if row.access_until and row.access_until > now else now
        row.access_until = base + timedelta(days=days)
        row.is_banned = False
        await session.commit()
        until = row.access_until
    _banned_users.discard(int(user_id))
    _access_cache[int(user_id)] = until
    return until


async def revoke_access(user_id: int) -> None:
    async with SessionLocal() as session:
        row = await session.get(BotUser, int(user_id))
        if row is not None:
            row.access_until = None
            await session.commit()
    _access_cache.pop(int(user_id), None)


async def set_banned(user_id: int, banned: bool) -> None:
    async with SessionLocal() as session:
        row = await session.get(BotUser, int(user_id))
        if row is None:
            row = BotUser(user_id=int(user_id), joined_at=datetime.utcnow(), last_seen_at=datetime.utcnow())
            session.add(row)
        row.is_banned = bool(banned)
        await session.commit()
    if banned:
        _banned_users.add(int(user_id))
    else:
        _banned_users.discard(int(user_id))


async def get_plans(*, active_only: bool = False) -> list[SubscriptionPlan]:
    async with SessionLocal() as session:
        stmt = select(SubscriptionPlan)
        if active_only:
            stmt = stmt.where(SubscriptionPlan.is_active.is_(True))
        stmt = stmt.order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.days.asc())
        return list((await session.execute(stmt)).scalars().all())


async def get_plan(key: str) -> SubscriptionPlan | None:
    async with SessionLocal() as session:
        return await session.get(SubscriptionPlan, key)


async def update_plan_price(key: str, price_usdt: float) -> SubscriptionPlan | None:
    price = round(float(price_usdt), 2)
    if price <= 0 or price > 100000:
        raise ValueError("invalid price")
    async with SessionLocal() as session:
        row = await session.get(SubscriptionPlan, key)
        if row is None:
            return None
        row.price_usdt = price
        row.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(row)
        return row


async def toggle_plan(key: str) -> SubscriptionPlan | None:
    async with SessionLocal() as session:
        row = await session.get(SubscriptionPlan, key)
        if row is None:
            return None
        row.is_active = not row.is_active
        row.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(row)
        return row


async def _crypto_create_invoice(amount: float, description: str, payload: str) -> InvoiceCreated:
    if not CRYPTO_PAY_TOKEN:
        raise PaymentProviderError("CryptoBot не настроен")
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    body = {
        "asset": "USDT",
        "amount": f"{amount:.2f}",
        "description": description[:1024],
        "payload": payload[:4096],
        "expires_in": PAYMENT_INVOICE_TTL_SECONDS,
        "allow_comments": False,
        "allow_anonymous": True,
    }
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        response = await client.post("https://pay.crypt.bot/api/createInvoice", headers=headers, json=body)
    try:
        data = response.json()
    except Exception as exc:
        raise PaymentProviderError(f"CryptoBot HTTP {response.status_code}") from exc
    if response.status_code >= 400 or not data.get("ok"):
        raise PaymentProviderError(str(data.get("error") or data.get("message") or f"CryptoBot HTTP {response.status_code}"))
    result = data.get("result") or {}
    ext_id = str(result.get("invoice_id") or "")
    pay_url = str(result.get("bot_invoice_url") or result.get("mini_app_invoice_url") or result.get("web_app_invoice_url") or result.get("pay_url") or "")
    if not ext_id or not pay_url:
        raise PaymentProviderError("CryptoBot вернул неполный invoice")
    return InvoiceCreated("cryptobot", ext_id, pay_url, str(result.get("status") or "active"))


async def _crypto_get_invoice(invoice_id: str) -> dict[str, Any] | None:
    if not CRYPTO_PAY_TOKEN:
        return None
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    params = {"invoice_ids": str(invoice_id), "count": 1}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get("https://pay.crypt.bot/api/getInvoices", headers=headers, params=params)
    if response.status_code >= 400:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    if not data.get("ok"):
        return None
    items = ((data.get("result") or {}).get("items") or [])
    return items[0] if items else None


async def _xrocket_create_invoice(amount: float, description: str, payload: str) -> InvoiceCreated:
    if not XROCKET_API_KEY:
        raise PaymentProviderError("xRocket не настроен")
    headers = {"Rocket-Pay-Key": XROCKET_API_KEY, "Content-Type": "application/json"}
    body = {
        "amount": round(amount, 2),
        "numPayments": 1,
        "currency": "USDT",
        "description": description[:1000],
        "payload": payload[:4000],
        "commentsEnabled": False,
        "expiredIn": PAYMENT_INVOICE_TTL_SECONDS,
    }
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        response = await client.post("https://pay.xrocket.tg/tg-invoices", headers=headers, json=body)
    try:
        data = response.json()
    except Exception as exc:
        raise PaymentProviderError(f"xRocket HTTP {response.status_code}") from exc
    if response.status_code >= 400 or data.get("success") is False:
        raise PaymentProviderError(str(data.get("message") or f"xRocket HTTP {response.status_code}"))
    result = data.get("data") or {}
    ext_id = str(result.get("id") or "")
    pay_url = str(result.get("link") or "")
    if not ext_id or not pay_url:
        raise PaymentProviderError("xRocket вернул неполный invoice")
    return InvoiceCreated("xrocket", ext_id, pay_url, str(result.get("status") or "active"))


async def _xrocket_get_invoice(invoice_id: str) -> dict[str, Any] | None:
    if not XROCKET_API_KEY:
        return None
    headers = {"Rocket-Pay-Key": XROCKET_API_KEY}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(f"https://pay.xrocket.tg/tg-invoices/{invoice_id}", headers=headers)
    if response.status_code >= 400:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    if data.get("success") is False:
        return None
    return data.get("data") or None


async def create_subscription_payment(user_id: int, plan_key: str, provider: str) -> SubscriptionPayment:
    plan = await get_plan(plan_key)
    if plan is None or not plan.is_active:
        raise PaymentProviderError("Тариф недоступен")
    if provider not in {"cryptobot", "xrocket"}:
        raise PaymentProviderError("Неизвестный способ оплаты")
    if not provider_enabled(provider):
        raise PaymentProviderError("Этот способ оплаты пока не настроен")

    payload = f"kleinanzeigen:{user_id}:{plan.key}:{int(datetime.utcnow().timestamp())}"
    description = f"Kleinanzeigen Parser — {plan.title}"
    if provider == "cryptobot":
        invoice = await _crypto_create_invoice(plan.price_usdt, description, payload)
    else:
        invoice = await _xrocket_create_invoice(plan.price_usdt, description, payload)

    async with SessionLocal() as session:
        row = SubscriptionPayment(
            user_id=int(user_id),
            plan_key=plan.key,
            provider=provider,
            external_invoice_id=invoice.external_id,
            amount_usdt=plan.price_usdt,
            status="pending",
            pay_url=invoice.pay_url,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(seconds=PAYMENT_INVOICE_TTL_SECONDS),
            raw_status=invoice.status,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def get_payment(payment_id: int) -> SubscriptionPayment | None:
    async with SessionLocal() as session:
        return await session.get(SubscriptionPayment, int(payment_id))


async def recent_payments(limit: int = 30) -> list[SubscriptionPayment]:
    async with SessionLocal() as session:
        return list((await session.execute(
            select(SubscriptionPayment).order_by(SubscriptionPayment.created_at.desc()).limit(limit)
        )).scalars().all())


async def pending_payments(limit: int = 100) -> list[SubscriptionPayment]:
    async with SessionLocal() as session:
        return list((await session.execute(
            select(SubscriptionPayment).where(SubscriptionPayment.status == "pending")
            .order_by(SubscriptionPayment.created_at.asc()).limit(limit)
        )).scalars().all())


async def _payment_lock(payment_id: int) -> asyncio.Lock:
    async with _payment_locks_guard:
        return _payment_locks.setdefault(int(payment_id), asyncio.Lock())


def _paid_invoice_matches(payment: SubscriptionPayment, remote: dict[str, Any]) -> bool:
    """Never activate access if the provider confirms a different amount/currency."""
    currency = str(remote.get("asset") or remote.get("currency") or "").upper()
    if currency != "USDT":
        return False
    try:
        remote_amount = Decimal(str(remote.get("amount")))
        expected = Decimal(str(payment.amount_usdt))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return abs(remote_amount - expected) <= Decimal("0.001")


async def refresh_payment(payment_id: int) -> tuple[SubscriptionPayment | None, bool]:
    """Refresh one invoice exactly once per process. Returns (payment, just_activated)."""
    lock = await _payment_lock(payment_id)
    async with lock:
        return await _refresh_payment_locked(payment_id)


async def _refresh_payment_locked(payment_id: int) -> tuple[SubscriptionPayment | None, bool]:
    """Provider check + idempotent subscription activation under the per-invoice lock."""
    payment = await get_payment(payment_id)
    if payment is None:
        return None, False
    if payment.status == "paid":
        return payment, False
    if payment.status in {"expired", "cancelled", "failed"}:
        return payment, False

    try:
        remote = await (_crypto_get_invoice(payment.external_invoice_id) if payment.provider == "cryptobot" else _xrocket_get_invoice(payment.external_invoice_id))
    except Exception:
        log.exception("Payment status check failed: %s/%s", payment.provider, payment.external_invoice_id)
        remote = None
    if not remote:
        # Never expire immediately just because our poll missed the provider at the
        # deadline: the user may have paid in time and the provider can be temporarily
        # unavailable. Give status reconciliation a short grace window.
        if payment.expires_at and payment.expires_at + timedelta(minutes=15) < datetime.utcnow():
            async with SessionLocal() as session:
                row = await session.get(SubscriptionPayment, payment.id)
                if row and row.status == "pending":
                    row.status = "expired"
                    row.raw_status = "local_expired_after_grace"
                    await session.commit()
            return await get_payment(payment.id), False
        return payment, False
    remote_status = str(remote.get("status") or "").lower()
    if remote_status not in {"paid", "expired"}:
        async with SessionLocal() as session:
            row = await session.get(SubscriptionPayment, payment.id)
            if row:
                row.raw_status = remote_status or row.raw_status
                await session.commit()
        return await get_payment(payment.id), False

    if remote_status == "expired":
        async with SessionLocal() as session:
            row = await session.get(SubscriptionPayment, payment.id)
            if row and row.status == "pending":
                row.status = "expired"
                row.raw_status = remote_status
                await session.commit()
        return await get_payment(payment.id), False

    if not _paid_invoice_matches(payment, remote):
        log.error(
            "Paid invoice verification failed provider=%s external_id=%s expected=%s remote_amount=%r remote_currency=%r",
            payment.provider, payment.external_invoice_id, payment.amount_usdt,
            remote.get("amount"), remote.get("asset") or remote.get("currency"),
        )
        async with SessionLocal() as session:
            row = await session.get(SubscriptionPayment, payment.id)
            if row and row.status == "pending":
                row.status = "failed"
                row.raw_status = "paid_verification_failed"
                await session.commit()
        return await get_payment(payment.id), False

    # Paid: transactionally activate once.
    async with SessionLocal() as session:
        row = await session.get(SubscriptionPayment, payment.id)
        if row is None:
            return None, False
        if row.status == "paid":
            return row, False
        plan = await session.get(SubscriptionPlan, row.plan_key)
        user = await session.get(BotUser, row.user_id)
        if plan is None:
            row.status = "failed"
            row.raw_status = "paid_but_plan_missing"
            await session.commit()
            return row, False
        if user is None:
            user = BotUser(user_id=row.user_id, joined_at=datetime.utcnow(), last_seen_at=datetime.utcnow())
            session.add(user)
        now = datetime.utcnow()
        base = user.access_until if user.access_until and user.access_until > now else now
        user.access_until = base + timedelta(days=plan.days)
        user.is_banned = False
        user.payments_count = int(user.payments_count or 0) + 1
        user.paid_total_usdt = float(user.paid_total_usdt or 0) + float(row.amount_usdt or 0)
        row.status = "paid"
        row.raw_status = remote_status
        row.paid_at = now
        await session.commit()
        until = user.access_until
        uid = user.user_id
    _banned_users.discard(uid)
    _access_cache[uid] = until
    return await get_payment(payment.id), True


async def admin_stats() -> dict[str, Any]:
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    async with SessionLocal() as session:
        total_users = (await session.execute(select(func.count(BotUser.user_id)))).scalar_one()
        active_users = (await session.execute(select(func.count(BotUser.user_id)).where(
            BotUser.is_banned.is_(False), BotUser.access_until.is_not(None), BotUser.access_until > now,
        ))).scalar_one()
        active_24h = (await session.execute(select(func.count(BotUser.user_id)).where(BotUser.last_seen_at >= day_ago))).scalar_one()
        new_24h = (await session.execute(select(func.count(BotUser.user_id)).where(BotUser.joined_at >= day_ago))).scalar_one()
        total_scans = (await session.execute(select(func.count(UserScan.id)))).scalar_one()
        scans_24h = (await session.execute(select(func.count(UserScan.id)).where(UserScan.created_at >= day_ago))).scalar_one()
        pending_count = (await session.execute(select(func.count(SubscriptionPayment.id)).where(
            SubscriptionPayment.status == "pending"
        ))).scalar_one()
        paid_count = (await session.execute(select(func.count(SubscriptionPayment.id)).where(
            SubscriptionPayment.status == "paid"
        ))).scalar_one()
        paid_total = (await session.execute(select(func.coalesce(func.sum(SubscriptionPayment.amount_usdt), 0.0)).where(
            SubscriptionPayment.status == "paid"
        ))).scalar_one()
        paid_24h = (await session.execute(select(func.coalesce(func.sum(SubscriptionPayment.amount_usdt), 0.0)).where(
            SubscriptionPayment.status == "paid", SubscriptionPayment.paid_at >= day_ago
        ))).scalar_one()
    return {
        "total_users": int(total_users or 0),
        "active_users": int(active_users or 0),
        "active_24h": int(active_24h or 0),
        "new_24h": int(new_24h or 0),
        "total_scans": int(total_scans or 0),
        "scans_24h": int(scans_24h or 0),
        "pending_payments": int(pending_count or 0),
        "paid_count": int(paid_count or 0),
        "paid_total": float(paid_total or 0),
        "paid_24h": float(paid_24h or 0),
    }
