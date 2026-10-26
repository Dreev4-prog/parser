"""Daily Telegram promotion posts. PostgreSQL owns configuration and send progress."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter,
)
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import String, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from commerce import current_access_mode
from db import SessionLocal
from models import AppSetting, BotUser, PromoPost, PromoRun


log = logging.getLogger("dt-parser-promotions")
MOSCOW = ZoneInfo("Europe/Moscow")
ROOT = Path(__file__).resolve().parent
SEED_KEY = "promo_posts_seed_v1"
ENABLED_KEY = "promo_posts_enabled_v1"
MUTE_PREFIX = "promo_mute:"
LEASE_SECONDS = 90
CATCHUP_MINUTES = 15
RUN_WINDOW_HOURS = 2
SEND_INTERVAL_SECONDS = 0.08
BUTTON_KINDS = {"parser", "radar", "subscription", "none"}
AUDIENCES = {"unsubscribed", "all"}

PARSER_CAPTION = (
    "👽 <b>Хватит жить в поиске Kleinanzeigen.</b>\n\n"
    "Открывать категории, листать объявления, проверять цены и просмотры — знакомый вечер?\n\n"
    "<b>DT Parser берёт отбор на себя.</b>\n\n"
    "🔎 Выбираешь категорию и условия поиска.\n"
    "⚙️ Парсер собирает подходящие объявления.\n"
    "📦 Получаешь результаты в Telegram — с ценами, просмотрами и ссылками.\n\n"
    "Меньше ручной работы. Больше времени разобраться в действительно интересных предложениях.\n\n"
    "<b>Твои фильтры. Твой поиск. Твой DT Parser.</b>\n\n"
    "💎 Открой подписку и запусти свой скан."
)
RADAR_CAPTION = (
    "👽 <b>Самая интересная товарка — та, которую ты даже не думал искать.</b>\n\n"
    "Ты заходишь посмотреть телефоны. А интерес покупателей уже растёт у кофемашины, "
    "инструмента или товара из совсем другой категории.\n\n"
    "<b>DT Radar помогает заметить такие находки.</b>\n\n"
    "📡 Отслеживает рост просмотров между собственными замерами.\n"
    "🔥 Выделяет горячие и набирающие товары.\n"
    "🗂 Показывает, в каких категориях появляется интерес.\n\n"
    "Товарка, мимо которой ты мог бы пролистнуть, уже ждёт твоего внимания в Radar.\n\n"
    "<b>Открой и посмотри, что ты сегодня упускаешь.</b>\n\n"
    "💎 Полная лента находок доступна по подписке."
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_time(value: str) -> str:
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", value or "")
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError("Укажи время в формате ЧЧ:ММ, например 17:00. Часовой пояс — Москва.")
    return f"{int(match[1]):02d}:{int(match[2]):02d}"


def scheduled_utc(post: PromoPost, now: datetime) -> datetime:
    local = now.replace(tzinfo=timezone.utc).astimezone(MOSCOW)
    hour, minute = map(int, post.time_msk.split(":"))
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def next_run_text(post: PromoPost, now: datetime | None = None, *, last_day: str = "") -> str:
    if not post.enabled or post.deleted:
        return "выключен"
    now = now or utcnow()
    due = scheduled_utc(post, now)
    due_day = due.replace(tzinfo=timezone.utc).astimezone(MOSCOW).date().isoformat()
    if due <= now or due < post.not_before or due_day == last_day:
        due += timedelta(days=1)
    return due.replace(tzinfo=timezone.utc).astimezone(MOSCOW).strftime("%d.%m в %H:%M МСК")


def _insert(session, model):
    return pg_insert(model) if session.get_bind().dialect.name == "postgresql" else sqlite_insert(model)


async def initialize_promotions(now: datetime | None = None) -> None:
    """Seed once only, so deleting/editing a default post survives redeploys."""
    now = now or utcnow()
    async with SessionLocal() as session:
        created = (await session.execute(_insert(session, AppSetting).values(
            key=SEED_KEY, value="1", updated_at=now,
        ).on_conflict_do_nothing(index_elements=[AppSetting.key]).returning(AppSetting.key))).scalar_one_or_none()
        if created:
            session.add_all([
                PromoPost(title="DT Parser · Альф", caption=PARSER_CAPTION,
                          photo="asset:promo_parser_alf.png", time_msk="12:00", button_kind="parser",
                          enabled=True, not_before=now, created_at=now, updated_at=now),
                PromoPost(title="DT Radar · Находки Альфа", caption=RADAR_CAPTION,
                          photo="asset:promo_radar_alf.png", time_msk="17:00", button_kind="radar",
                          enabled=True, not_before=now, created_at=now, updated_at=now),
            ])
            await session.execute(_insert(session, AppSetting).values(
                key=ENABLED_KEY, value="1", updated_at=now,
            ).on_conflict_do_nothing(index_elements=[AppSetting.key]))
        await session.commit()


async def promotions_enabled() -> bool:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, ENABLED_KEY)
        return row is not None and row.value == "1"


async def set_promotions_enabled(enabled: bool) -> None:
    async with SessionLocal() as session:
        await session.execute(_insert(session, AppSetting).values(
            key=ENABLED_KEY, value="1" if enabled else "0", updated_at=utcnow(),
        ).on_conflict_do_update(index_elements=[AppSetting.key], set_={
            "value": "1" if enabled else "0", "updated_at": utcnow(),
        }))
        await session.commit()


async def list_posts(page: int = 0) -> tuple[list[PromoPost], int]:
    async with SessionLocal() as session:
        total = int((await session.execute(select(func.count(PromoPost.id)).where(
            PromoPost.deleted.is_(False),
        ))).scalar_one())
        rows = list((await session.execute(select(PromoPost).where(
            PromoPost.deleted.is_(False),
        ).order_by(PromoPost.time_msk, PromoPost.id).offset(max(0, page) * 10).limit(10))).scalars())
        return rows, total


async def get_post(post_id: int) -> PromoPost | None:
    async with SessionLocal() as session:
        post = await session.get(PromoPost, post_id)
        return post if post and not post.deleted else None


async def create_post(**values) -> PromoPost:
    values["time_msk"] = normalize_time(values.get("time_msk", "12:00"))
    post = PromoPost(**values, enabled=False, not_before=utcnow())
    async with SessionLocal() as session:
        session.add(post)
        await session.commit()
    return post


async def edit_post(post_id: int, **values) -> PromoPost | None:
    allowed_fields = {"title", "caption", "photo", "time_msk", "button_kind", "audience", "enabled", "deleted"}
    if set(values) - allowed_fields:
        raise ValueError("Неизвестное поле поста")
    if "time_msk" in values:
        values["time_msk"] = normalize_time(values["time_msk"])
    if "button_kind" in values and values["button_kind"] not in BUTTON_KINDS:
        raise ValueError("Неизвестная кнопка")
    if "audience" in values and values["audience"] not in AUDIENCES:
        raise ValueError("Неизвестная аудитория")
    now = utcnow()
    if values.get("enabled") or "time_msk" in values:
        values["not_before"] = now
    async with SessionLocal() as session:
        post = await session.get(PromoPost, post_id)
        if not post or post.deleted:
            return None
        for key, value in values.items():
            setattr(post, key, value)
        post.updated_at = now
        await session.commit()
        return post


async def latest_run(post_id: int) -> PromoRun | None:
    async with SessionLocal() as session:
        return (await session.execute(select(PromoRun).where(
            PromoRun.post_id == post_id,
        ).order_by(PromoRun.id.desc()).limit(1))).scalar_one_or_none()


async def mute_promotions(user_id: int, muted: bool = True) -> None:
    async with SessionLocal() as session:
        key = f"{MUTE_PREFIX}{int(user_id)}"
        if muted:
            await session.execute(_insert(session, AppSetting).values(
                key=key, value="1", updated_at=utcnow(),
            ).on_conflict_do_nothing(index_elements=[AppSetting.key]))
        else:
            await session.execute(delete(AppSetting).where(AppSetting.key == key))
        await session.commit()


def post_keyboard(kind: str, *, preview: bool = False) -> InlineKeyboardMarkup | None:
    buttons = []
    if kind == "parser":
        buttons.append([InlineKeyboardButton(text="🚀 Открыть DT Parser", callback_data="start_scan")])
    elif kind == "radar":
        buttons.append([InlineKeyboardButton(text="🔥 Посмотреть находки Radar", callback_data="radar_home")])
    if kind != "none":
        buttons.append([InlineKeyboardButton(text="💎 Открыть подписку", callback_data="subscription")])
    if not preview:
        buttons.append([InlineKeyboardButton(text="🔕 Не присылать промопосты", callback_data="promo_mute")])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


async def send_post(bot: Bot, chat_id: int, payload: dict, *, preview: bool = False):
    photo = str(payload.get("photo") or "")
    markup = post_keyboard(payload.get("button_kind", "subscription"), preview=preview)
    if photo:
        if photo.startswith("asset:"):
            # Only bundled filenames, never a path received from an admin message.
            name = photo.removeprefix("asset:")
            if name not in {"promo_parser_alf.png", "promo_radar_alf.png"}:
                raise ValueError("Неизвестная картинка поста")
            path = ROOT / "assets" / name
            if not path.is_file():
                raise FileNotFoundError(f"Отсутствует assets/{name}")
            photo = FSInputFile(path)
        return await bot.send_photo(chat_id, photo=photo, caption=payload.get("caption") or None,
                                    parse_mode="HTML", reply_markup=markup)
    return await bot.send_message(chat_id, payload.get("caption") or "DT Parser",
                                  parse_mode="HTML", reply_markup=markup)


def post_payload(post: PromoPost) -> dict:
    return {key: getattr(post, key) for key in ("title", "caption", "photo", "button_kind", "audience")}


async def create_due_runs(now: datetime | None = None) -> None:
    now = now or utcnow()
    async with SessionLocal() as session:
        posts = (await session.execute(select(PromoPost).where(
            PromoPost.enabled.is_(True), PromoPost.deleted.is_(False),
        ))).scalars().all()
        for post in posts:
            due = scheduled_utc(post, now)
            if due < post.not_before or not due <= now < due + timedelta(minutes=CATCHUP_MINUTES):
                continue
            day = due.replace(tzinfo=timezone.utc).astimezone(MOSCOW).date().isoformat()
            await session.execute(_insert(session, PromoRun).values(
                post_id=post.id, day_msk=day, scheduled_at=due,
                deadline_at=due + timedelta(hours=RUN_WINDOW_HOURS),
                payload=json.dumps(post_payload(post), ensure_ascii=False), status="running",
                created_at=now, updated_at=now,
            ).on_conflict_do_nothing(index_elements=[PromoRun.post_id, PromoRun.day_msk]))
        await session.commit()


async def claim_run(owner: str, now: datetime | None = None) -> PromoRun | None:
    now = now or utcnow()
    async with SessionLocal() as session:
        await session.execute(update(PromoRun).where(
            PromoRun.status == "running", PromoRun.deadline_at <= now,
            or_(PromoRun.lease_until.is_(None), PromoRun.lease_until <= now),
        ).values(status="expired", updated_at=now))
        run_id = (await session.execute(select(PromoRun.id).join(
            PromoPost, PromoPost.id == PromoRun.post_id,
        ).where(
            PromoRun.status == "running", PromoRun.deadline_at > now,
            PromoPost.enabled.is_(True), PromoPost.deleted.is_(False),
            or_(PromoRun.lease_until.is_(None), PromoRun.lease_until <= now),
        ).order_by(PromoRun.scheduled_at, PromoRun.id).limit(1))).scalar_one_or_none()
        run = None
        if run_id is not None:
            run = (await session.execute(update(PromoRun).where(
                PromoRun.id == run_id, PromoRun.status == "running",
                or_(PromoRun.lease_until.is_(None), PromoRun.lease_until <= now),
            ).values(lease_owner=owner, lease_until=now + timedelta(seconds=LEASE_SECONDS),
                     updated_at=now).returning(PromoRun))).scalar_one_or_none()
        await session.commit()
        return run


async def renew_lease(run_id: int, owner: str) -> bool:
    now = utcnow()
    async with SessionLocal() as session:
        result = await session.execute(update(PromoRun).where(
            PromoRun.id == run_id, PromoRun.lease_owner == owner, PromoRun.status == "running",
            PromoRun.lease_until > now,
        ).values(lease_until=now + timedelta(seconds=LEASE_SECONDS), updated_at=now))
        await session.commit()
        return bool(result.rowcount)


async def reserve_recipient(run_id: int, owner: str, user_id: int) -> bool:
    now = utcnow()
    async with SessionLocal() as session:
        result = await session.execute(update(PromoRun).where(
            PromoRun.id == run_id, PromoRun.lease_owner == owner,
            PromoRun.status == "running", PromoRun.lease_until > now,
            PromoRun.cursor_user_id < user_id,
        ).values(cursor_user_id=user_id, processed=PromoRun.processed + 1, updated_at=now))
        await session.commit()
        return bool(result.rowcount)


def _eligible(audience: str, now: datetime, admin_ids: set[int]):
    conditions = [BotUser.user_id > 0, BotUser.is_banned.is_(False),
                  ~exists().where(AppSetting.key == MUTE_PREFIX + BotUser.user_id.cast(String))]
    if admin_ids:
        conditions.append(BotUser.user_id.notin_(sorted(admin_ids)))
    if audience == "unsubscribed":
        conditions.append(or_(BotUser.access_until.is_(None), BotUser.access_until <= now))
    return conditions


async def recipients_after(run: PromoRun, payload: dict, after: int, admin_ids: set[int]) -> list[int]:
    async with SessionLocal() as session:
        return list((await session.execute(select(BotUser.user_id).where(
            *_eligible(payload["audience"], utcnow(), admin_ids),
            BotUser.joined_at <= run.scheduled_at, BotUser.user_id > after,
        ).order_by(BotUser.user_id).limit(100))).scalars())


async def still_eligible(user_id: int, audience: str, admin_ids: set[int]) -> bool:
    async with SessionLocal() as session:
        return (await session.execute(select(BotUser.user_id).where(
            BotUser.user_id == user_id, *_eligible(audience, utcnow(), admin_ids),
        ))).scalar_one_or_none() is not None


async def _outcome(run_id: int, owner: str, result: str) -> None:
    field = getattr(PromoRun, result)
    async with SessionLocal() as session:
        await session.execute(update(PromoRun).where(
            PromoRun.id == run_id, PromoRun.lease_owner == owner,
        ).values({result: field + 1, "updated_at": utcnow()}))
        await session.commit()


async def _finish(run_id: int, owner: str, status: str, error: str = "") -> None:
    async with SessionLocal() as session:
        await session.execute(update(PromoRun).where(
            PromoRun.id == run_id, PromoRun.lease_owner == owner,
        ).values(status=status, lease_owner="", lease_until=None,
                 last_error=error[:160], updated_at=utcnow()))
        await session.commit()


async def process_run(bot: Bot, run: PromoRun, owner: str, admin_ids: set[int]) -> None:
    payload = json.loads(run.payload)
    cursor = int(run.cursor_user_id)
    lease_lost = asyncio.Event()

    async def heartbeat():
        while True:
            await asyncio.sleep(20)
            try:
                if not await renew_lease(run.id, owner):
                    lease_lost.set()
                    return
            except Exception:
                lease_lost.set()
                log.exception("Promotion lease heartbeat failed run=%s", run.id)
                return

    pulse = asyncio.create_task(heartbeat(), name=f"promo-lease-{run.id}")
    try:
        while not lease_lost.is_set():
            post = await get_post(run.post_id)
            if not post:
                await _finish(run.id, owner, "cancelled")
                return
            if not post.enabled or not await promotions_enabled() or current_access_mode() != "subscription":
                await _finish(run.id, owner, "running")
                return
            if utcnow() >= run.deadline_at:
                await _finish(run.id, owner, "expired")
                return
            recipients = await recipients_after(run, payload, cursor, admin_ids)
            if not recipients:
                await _finish(run.id, owner, "done")
                log.info("Promotion completed post=%s day=%s", run.post_id, run.day_msk)
                return
            for user_id in recipients:
                if lease_lost.is_set() or utcnow() >= run.deadline_at:
                    return
                # Disabling/deleting a post or globally pausing takes effect before
                # the next recipient. Subscription/mute checks are also fresh.
                post = await get_post(run.post_id)
                if (not post or not post.enabled or not await promotions_enabled()
                        or current_access_mode() != "subscription"):
                    await _finish(run.id, owner, "running" if post else "cancelled")
                    return
                if not await reserve_recipient(run.id, owner, user_id):
                    return
                cursor = user_id
                if not await still_eligible(user_id, payload["audience"], admin_ids):
                    await _outcome(run.id, owner, "skipped")
                    continue
                try:
                    for attempt in range(3):
                        try:
                            message = await send_post(bot, user_id, payload)
                            break
                        except TelegramRetryAfter as exc:
                            # A 429 explicitly rejects this send; unlike a network
                            # timeout it is safe to retry after the requested delay.
                            if attempt == 2 or utcnow() + timedelta(seconds=exc.retry_after) >= run.deadline_at:
                                raise
                            await asyncio.sleep(float(exc.retry_after) + 0.25)
                            if lease_lost.is_set():
                                return
                            post = await get_post(run.post_id)
                            if (not post or not post.enabled or not await promotions_enabled()
                                    or current_access_mode() != "subscription"):
                                await _outcome(run.id, owner, "skipped")
                                await _finish(run.id, owner, "running" if post else "cancelled")
                                return
                            if not await still_eligible(user_id, payload["audience"], admin_ids):
                                message = None
                                break
                    if message is None:
                        await _outcome(run.id, owner, "skipped")
                        continue
                    await _outcome(run.id, owner, "sent")
                    # Reuse Telegram's file_id after the first upload in this run.
                    if getattr(message, "photo", None):
                        payload["photo"] = message.photo[-1].file_id
                except TelegramForbiddenError:
                    await _outcome(run.id, owner, "blocked")
                    await mute_promotions(user_id)
                except TelegramNetworkError:
                    # Telegram may have accepted the message before the connection
                    # was lost. Do not replay it; processed - outcomes exposes this.
                    log.warning("Promotion delivery uncertain run=%s user=%s", run.id, user_id)
                except TelegramBadRequest as exc:
                    await _outcome(run.id, owner, "failed")
                    reason = str(exc).lower()
                    if "chat not found" in reason or "user is deactivated" in reason:
                        await mute_promotions(user_id)
                    else:
                        await _finish(run.id, owner, "error", type(exc).__name__ + ": " + str(exc))
                        return
                except (FileNotFoundError, ValueError) as exc:
                    await _outcome(run.id, owner, "failed")
                    await _finish(run.id, owner, "error", str(exc))
                    return
                except TelegramRetryAfter:
                    await _outcome(run.id, owner, "failed")
                await asyncio.sleep(SEND_INTERVAL_SECONDS)
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        # Release only our own lease. An interrupted send's committed recipient
        # cursor prevents it from being replayed on a later scheduler tick.
        async with SessionLocal() as session:
            await session.execute(update(PromoRun).where(
                PromoRun.id == run.id, PromoRun.lease_owner == owner,
            ).values(lease_owner="", lease_until=None))
            await session.commit()


async def prune_promo_runs() -> None:
    async with SessionLocal() as session:
        old = select(PromoRun.id).where(
            PromoRun.updated_at < utcnow() - timedelta(days=30),
            PromoRun.status != "running",
        ).limit(500)
        await session.execute(delete(PromoRun).where(PromoRun.id.in_(old)))
        await session.commit()


async def promotion_scheduler(bot: Bot, admin_ids: set[int]) -> None:
    owner = uuid.uuid4().hex
    last_cleanup = 0.0
    active: asyncio.Task | None = None
    try:
        while True:
            try:
                if active and active.done():
                    try:
                        active.result()
                    except Exception:
                        log.exception("Promotion send task failed")
                    active = None
                if await promotions_enabled() and current_access_mode() == "subscription":
                    # Keep recording due slots even while a previous post is
                    # broadcasting, so adjacent admin-selected times aren't lost.
                    await create_due_runs()
                    if active is None:
                        run = await claim_run(owner)
                        if run:
                            active = asyncio.create_task(process_run(bot, run, owner, admin_ids),
                                                         name=f"promo-send-{run.id}")
                if time.monotonic() - last_cleanup > 86400:
                    await prune_promo_runs()
                    last_cleanup = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scheduled promotion tick failed")
            await asyncio.sleep(15)
    finally:
        if active:
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
