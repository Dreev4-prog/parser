from __future__ import annotations

import asyncio
import csv
import html
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from categories import CATEGORIES, GROUPS, categories_for_group, group_root_key
from db import SessionLocal, USING_PERSISTENT_DATABASE, init_db
from filters import (
    base_filter,
    below_market_rows,
    dedupe_rows,
    disappearing_rows,
    frequent_rows,
    price_drop_rows,
    sort_rows,
    unique_rows,
)
from models import CategoryScanState, Listing, ParserRun, PriceHistory, ScanListing, SelectedCategory, UserScan, UserSettings, ViewHistory
from parser import (
    MAX_PAGES_PER_CATEGORY,
    PAGE_DELAY_SECONDS,
    STOP_AFTER_EMPTY_TODAY_PAGES,
    KleinanzeigenParser,
    ParsedListing,
    ViewCountResult,
    TemporaryAccessError,
    is_today_text,
    page_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("kleinanzeigen-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
BERLIN = ZoneInfo("Europe/Berlin")
AVAILABILITY_CHECK_LIMIT = max(1, int(os.getenv("AVAILABILITY_CHECK_LIMIT", "150")))
AVAILABILITY_CONCURRENCY = max(1, min(8, int(os.getenv("AVAILABILITY_CONCURRENCY", "4"))))

# v2.6 Multi-User Core. User launches go into a queue. Only a limited number
# of jobs are processed at once, while category scans are shared globally.
MAX_CONCURRENT_JOBS = max(1, min(8, int(os.getenv("MAX_CONCURRENT_JOBS", "3"))))
MAX_QUEUE_SIZE = max(10, int(os.getenv("MAX_QUEUE_SIZE", "200")))
CATEGORY_CACHE_TTL_SECONDS = max(0, int(os.getenv("CATEGORY_CACHE_TTL_SECONDS", "300")))
STATUS_UPDATE_INTERVAL_SECONDS = max(0.5, float(os.getenv("STATUS_UPDATE_INTERVAL_SECONDS", "1.5")))

# Public view counts are collected inline while category pages are scanned.
# Recent values are cached so shared/multi-user scans do not reopen the same ad.
VIEW_COUNT_CACHE_TTL_SECONDS = max(60, int(os.getenv("VIEW_COUNT_CACHE_TTL_SECONDS", "1800")))
VIEW_COUNT_CONCURRENCY = max(1, min(10, int(os.getenv("VIEW_COUNT_CONCURRENCY", "5"))))
VIEW_COUNT_EXPORT_MODES = {"newest", "all", "unique", "below_market"}

# v2.5 incremental scan tuning (kept in v2.6). A full scan is forced once per category per Berlin day.
# Later runs stop after the parser crosses the previous head checkpoint and then
# sees a small safety overlap of already-known pages.
INCREMENTAL_STOP_AFTER_KNOWN_PAGES = max(1, int(os.getenv("INCREMENTAL_STOP_AFTER_KNOWN_PAGES", "2")))
INCREMENTAL_MIN_KNOWN_RATIO = min(1.0, max(0.5, float(os.getenv("INCREMENTAL_MIN_KNOWN_RATIO", "0.80"))))
INCREMENTAL_MIN_PAGES = max(1, int(os.getenv("INCREMENTAL_MIN_PAGES", "2")))
INCREMENTAL_HEAD_SIZE = max(3, min(20, int(os.getenv("INCREMENTAL_HEAD_SIZE", "8"))))
INCREMENTAL_OVERLAP_PAGES = max(0, int(os.getenv("INCREMENTAL_OVERLAP_PAGES", "1")))

MODE_LABELS = {
    "newest": "🆕 Самые новые",
    "all": "📚 Все",
    "unique": "💎 Уникальные",
    "frequent": "🔥 Часто публикуемые",
    "below_market": "💰 Ниже рынка",
    "fast_disappearing": "⚡ Быстро исчезающие",
    "price_drop": "📉 Снижение цены",
}
PERIOD_LABELS = {"1h": "1 час", "3h": "3 часа", "6h": "6 часов", "today": "Сегодня"}
PRICE_LABELS = {
    "any": "Любая",
    "0_50": "0–50 €",
    "50_100": "50–100 €",
    "100_200": "100–200 €",
    "200_500": "200–500 €",
    "500_plus": "500+ €",
}
SORT_LABELS = {"newest": "Сначала новые", "price_asc": "Цена ↑", "price_desc": "Цена ↓"}
PAGE_LIMIT_CHOICES = (25, 50, 100)
# Conservative baseline for user-facing ETA. A full 100-page category starts
# around 3 minutes, then the estimate is recalculated from the real page rate.
PAGE_LIMIT_BASE_ETA_SECONDS = {25: 60, 50: 120, 100: 180}


class SettingsInput(StatesGroup):
    include_words = State()
    exclude_words = State()
    view_test_url = State()


def allowed(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def main_keyboard(selected_count: int = 0) -> InlineKeyboardMarkup:
    """Simple user-facing home screen. Technical/debug actions stay out of the way."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Популярное сейчас", callback_data="popular_now")],
        [InlineKeyboardButton(text="🔎 Новый скан", callback_data="start_scan")],
        [InlineKeyboardButton(text="📊 Мои сканы", callback_data="my_scans")],
        [InlineKeyboardButton(text=f"🗂 Категории ({selected_count})", callback_data="groups"),
         InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="📦 Текущий результат", callback_data="export_smart")],
    ])


def post_scan_keyboard(scan_id: int | None = None) -> InlineKeyboardMarkup:
    """Actions shown under the automatic result file."""
    rows = []
    if scan_id is not None:
        rows.append([InlineKeyboardButton(text="📊 Открыть этот скан", callback_data=f"scan:{scan_id}")])
        rows.append([
            InlineKeyboardButton(text="👁 Обновить просмотры", callback_data=f"scanviews:{scan_id}"),
            InlineKeyboardButton(text="🔄 Повторить", callback_data=f"scanrepeat:{scan_id}"),
        ])
        rows.append([
            InlineKeyboardButton(text="🔥 Топ", callback_data=f"scantop:{scan_id}"),
            InlineKeyboardButton(text="🚀 Рост", callback_data=f"scangrowth:{scan_id}"),
        ])
    else:
        rows.append([InlineKeyboardButton(text="🔄 Запустить снова", callback_data="start_scan")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="post_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def scan_detail_keyboard(scan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Обновить просмотры", callback_data=f"scanviews:{scan_id}"),
         InlineKeyboardButton(text="🔄 Пересканировать", callback_data=f"scanrepeat:{scan_id}")],
        [InlineKeyboardButton(text="🔥 Самые просматриваемые", callback_data=f"scantop:{scan_id}")],
        [InlineKeyboardButton(text="🚀 Сильнее всего растут", callback_data=f"scangrowth:{scan_id}")],
        [InlineKeyboardButton(text="📄 Файл этого скана", callback_data=f"scanexport:{scan_id}")],
        [InlineKeyboardButton(text="⬅️ Мои сканы", callback_data="my_scans"),
         InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
    ])


class BotChatAdapter:
    """Small duck-typed adapter so export code can send to a chat after a queue job."""

    def __init__(self, bot: Bot, chat_id: int, *, prefix: str = "", reply_markup: InlineKeyboardMarkup | None = None):
        self.bot = bot
        self.chat_id = chat_id
        self.prefix = prefix.strip()
        self.reply_markup = reply_markup

    async def answer(self, text: str, **kwargs):
        if self.prefix:
            text = f"{self.prefix}\n\n{text}"
        if self.reply_markup is not None:
            kwargs["reply_markup"] = self.reply_markup
        kwargs.setdefault("parse_mode", ParseMode.HTML)
        return await self.bot.send_message(self.chat_id, text, **kwargs)

    async def answer_document(self, document, *, caption: str | None = None, **kwargs):
        full_caption = caption or ""
        if self.prefix:
            full_caption = f"{self.prefix}\n\n{full_caption}" if full_caption else self.prefix
        if self.reply_markup is not None:
            kwargs["reply_markup"] = self.reply_markup
        kwargs.setdefault("parse_mode", ParseMode.HTML)
        return await self.bot.send_document(self.chat_id, document, caption=full_caption, **kwargs)


def groups_keyboard(selected_keys: set[str]) -> InlineKeyboardMarkup:
    rows = []
    items = list(GROUPS.values())
    for i in range(0, len(items), 2):
        row = []
        for group in items[i:i+2]:
            count = sum(1 for c in CATEGORIES.values() if c.group == group.key and c.key in selected_keys)
            suffix = f" · {count}" if count else ""
            row.append(InlineKeyboardButton(text=f"{group.icon} {group.name}{suffix}", callback_data=f"grp:{group.key}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🧹 Очистить выбор", callback_data="clear_all")])
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_keyboard(group_key: str, selected_keys: set[str]) -> InlineKeyboardMarkup:
    cats = categories_for_group(group_key)
    rows = []
    for cat in cats:
        marker = "✅" if cat.key in selected_keys else "▫️"
        rows.append([InlineKeyboardButton(text=f"{marker} {cat.name}", callback_data=f"cat:{cat.key}")])
    child_keys = [c.key for c in cats if not c.is_group]
    children_all = bool(child_keys) and all(k in selected_keys for k in child_keys)
    rows.append([InlineKeyboardButton(
        text=("☑️ Убрать все подкатегории" if children_all else "☑️ Выбрать все подкатегории"),
        callback_data=f"grpall:{group_key}",
    )])
    rows.append([InlineKeyboardButton(text="⬅️ К разделам", callback_data="groups")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mode_button(s: UserSettings, mode: str) -> InlineKeyboardButton:
    label = MODE_LABELS[mode]
    if s.output_mode == mode:
        label = "✅ " + label
    return InlineKeyboardButton(text=label, callback_data=f"quickmode:{mode}")


def settings_keyboard(s: UserSettings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_mode_button(s, "newest"), _mode_button(s, "all")],
        [_mode_button(s, "unique"), _mode_button(s, "frequent")],
        [_mode_button(s, "fast_disappearing"), _mode_button(s, "below_market")],
        [_mode_button(s, "price_drop")],
        [InlineKeyboardButton(text=f"🚫 Умные дубли: {'ВКЛ' if s.smart_dedupe else 'ВЫКЛ'}", callback_data="toggle_dedupe")],
        [InlineKeyboardButton(text=f"🧹 Чистить услуги/поиск: {'ВКЛ' if s.clean_noise else 'ВЫКЛ'}", callback_data="toggle_noise")],
        [InlineKeyboardButton(text=f"🕐 Период: {PERIOD_LABELS.get(s.period, s.period)}", callback_data="set_period")],
        [InlineKeyboardButton(text=f"💶 Цена: {PRICE_LABELS.get(s.price_filter, s.price_filter)}", callback_data="set_price")],
        [InlineKeyboardButton(text=f"↕️ Сортировка: {SORT_LABELS.get(s.sort_mode, s.sort_mode)}", callback_data="set_sort")],
        [InlineKeyboardButton(text="✅ Ключевые слова", callback_data="set_include"),
         InlineKeyboardButton(text="🚫 Исключить слова", callback_data="set_exclude")],
        [InlineKeyboardButton(text="ℹ️ Как работают режимы", callback_data="mode_help")],
        [InlineKeyboardButton(text="♻️ Сбросить настройки", callback_data="reset_settings")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")],
    ])


def page_limit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="25 страниц", callback_data="scanpages:25"),
         InlineKeyboardButton(text="50 страниц", callback_data="scanpages:50")],
        [InlineKeyboardButton(text="100 страниц", callback_data="scanpages:100")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")],
    ])


def choice_keyboard(prefix: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"{prefix}:{value}")] for value, label in options]
    rows.append([InlineKeyboardButton(text="⬅️ К настройкам", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_settings(user_id: int) -> UserSettings:
    async with SessionLocal() as session:
        s = await session.get(UserSettings, user_id)
        if s is None:
            s = UserSettings(user_id=user_id)
            session.add(s)
            await session.commit()
            await session.refresh(s)
        return s


async def update_setting(user_id: int, field: str, value) -> UserSettings:
    async with SessionLocal() as session:
        s = await session.get(UserSettings, user_id)
        if s is None:
            s = UserSettings(user_id=user_id)
            session.add(s)
        setattr(s, field, value)
        await session.commit()
        await session.refresh(s)
        return s


async def reset_user_settings(user_id: int) -> UserSettings:
    async with SessionLocal() as session:
        old = await session.get(UserSettings, user_id)
        if old:
            await session.delete(old)
            await session.commit()
    return await get_settings(user_id)


async def get_selected(user_id: int) -> set[str]:
    async with SessionLocal() as session:
        result = await session.execute(select(SelectedCategory.category_key).where(SelectedCategory.user_id == user_id))
        return {x for x in result.scalars().all() if x in CATEGORIES}


def _scan_category_keys(scan: UserScan) -> list[str]:
    return [x for x in (scan.category_keys or "").split(",") if x]


def _scan_title(keys: list[str]) -> str:
    names = [CATEGORIES[k].name for k in keys if k in CATEGORIES]
    if not names:
        return "Скан Kleinanzeigen"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} + {names[1]}"
    return f"{names[0]} + ещё {len(names) - 1}"


async def create_user_scan(user_id: int, job_uid: str, category_keys: list[str], page_limit: int) -> UserScan:
    scan = UserScan(
        job_uid=job_uid,
        user_id=user_id,
        title=_scan_title(category_keys),
        category_keys=",".join(category_keys),
        page_limit=page_limit,
        status="queued",
        total_categories=len(category_keys),
    )
    async with SessionLocal() as session:
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        return scan


async def get_user_scan(user_id: int, scan_id: int) -> UserScan | None:
    async with SessionLocal() as session:
        result = await session.execute(select(UserScan).where(UserScan.id == scan_id, UserScan.user_id == user_id))
        return result.scalar_one_or_none()


async def get_user_scans(user_id: int, limit: int = 10) -> list[UserScan]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(UserScan).where(UserScan.user_id == user_id).order_by(UserScan.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())


async def get_scan_rows(scan_id: int) -> list[tuple[Listing, ScanListing]]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing, ScanListing)
            .join(ScanListing, Listing.external_id == ScanListing.external_id)
            .where(ScanListing.scan_id == scan_id)
        )
        return list(result.all())


async def finalize_user_scan(job: "ScanJob", *, cancelled: bool = False) -> None:
    if job.scan_id is None:
        return
    now = datetime.utcnow()
    async with db_write_lock:
        async with SessionLocal() as session:
            scan = await session.get(UserScan, job.scan_id)
            if scan is None:
                return
            scan.status = "cancelled" if cancelled else "done"
            scan.finished_at = now
            scan.completed_categories = job.completed_categories
            scan.total_categories = len(job.category_keys)
            scan.new_count = job.total_new
            if cancelled:
                await session.commit()
                return

            start_utc, end_utc = berlin_today_utc_bounds()
            result = await session.execute(select(Listing).where(
                Listing.category_key.in_(job.category_keys),
                Listing.first_seen_at >= start_utc,
                Listing.first_seen_at < end_utc,
            ))
            rows = list(result.scalars().all())
            scan.result_count = len(rows)
            scan.viewed_count = sum(1 for row in rows if row.view_count is not None)
            scan.last_view_refresh_at = now if scan.viewed_count else None

            await session.execute(delete(ScanListing).where(ScanListing.scan_id == scan.id))
            for row in rows:
                session.add(ScanListing(
                    scan_id=scan.id,
                    external_id=row.external_id,
                    initial_view_count=row.view_count,
                    captured_at=now,
                ))
            await session.commit()


async def update_scan_view_refresh(scan_id: int) -> None:
    async with SessionLocal() as session:
        scan = await session.get(UserScan, scan_id)
        if scan is None:
            return
        pairs = await session.execute(
            select(Listing.view_count).join(ScanListing, Listing.external_id == ScanListing.external_id)
            .where(ScanListing.scan_id == scan_id)
        )
        values = [v for v in pairs.scalars().all()]
        scan.viewed_count = sum(1 for v in values if v is not None)
        scan.last_view_refresh_at = datetime.utcnow()
        await session.commit()


def my_scans_keyboard(scans: list[UserScan]) -> InlineKeyboardMarkup:
    rows = []
    for scan in scans[:8]:
        icon = "✅" if scan.status == "done" else ("⏳" if scan.status in {"queued", "running"} else "⚪️")
        stamp = _berlin_text(scan.finished_at or scan.created_at)
        short_time = f"{stamp[:5]} {stamp[-5:]}" if len(stamp) >= 16 else stamp
        label = f"{icon} {scan.title[:24]} · {short_time}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"scan:{scan.id}")])
    rows.append([InlineKeyboardButton(text="🔎 Новый скан", callback_data="start_scan")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def toggle_category(user_id: int, key: str) -> set[str]:
    cat = CATEGORIES[key]
    root_key = group_root_key(cat.group)
    async with SessionLocal() as session:
        result = await session.execute(select(SelectedCategory).where(
            SelectedCategory.user_id == user_id, SelectedCategory.category_key == key,
        ))
        existing = result.scalar_one_or_none()
        if existing:
            await session.delete(existing)
        else:
            if cat.is_group:
                child_keys = [c.key for c in categories_for_group(cat.group) if not c.is_group]
                if child_keys:
                    await session.execute(delete(SelectedCategory).where(
                        SelectedCategory.user_id == user_id, SelectedCategory.category_key.in_(child_keys),
                    ))
            else:
                await session.execute(delete(SelectedCategory).where(
                    SelectedCategory.user_id == user_id, SelectedCategory.category_key == root_key,
                ))
            session.add(SelectedCategory(user_id=user_id, category_key=key))
        await session.commit()
    return await get_selected(user_id)


async def toggle_group_children(user_id: int, group_key: str) -> set[str]:
    child_keys = [c.key for c in categories_for_group(group_key) if not c.is_group]
    selected = await get_selected(user_id)
    all_selected = bool(child_keys) and all(k in selected for k in child_keys)
    async with SessionLocal() as session:
        await session.execute(delete(SelectedCategory).where(
            SelectedCategory.user_id == user_id, SelectedCategory.category_key == group_root_key(group_key),
        ))
        if all_selected:
            await session.execute(delete(SelectedCategory).where(
                SelectedCategory.user_id == user_id, SelectedCategory.category_key.in_(child_keys),
            ))
        else:
            missing = [k for k in child_keys if k not in selected]
            session.add_all([SelectedCategory(user_id=user_id, category_key=k) for k in missing])
        await session.commit()
    return await get_selected(user_id)


async def clear_selected(user_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(SelectedCategory).where(SelectedCategory.user_id == user_id))
        await session.commit()


async def upsert_page_items(
    category_key: str, category_name: str, items: list[ParsedListing]
) -> tuple[list[ParsedListing], int, int]:
    if not items:
        return [], 0, 0
    unique = {item.external_id: item for item in items}
    ids = list(unique)
    async with SessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.external_id.in_(ids)))
        existing = {row.external_id: row for row in result.scalars().all()}
        now = datetime.utcnow()
        new_items: list[ParsedListing] = []
        enriched_count = 0
        for external_id, item in unique.items():
            row = existing.get(external_id)
            if row is None:
                session.add(Listing(
                    external_id=item.external_id, category_key=category_key, category=category_name,
                    title=item.title, price_text=item.price_text, price_eur=item.price_eur,
                    posted_text=item.posted_text, url=item.url, first_seen_at=now, last_seen_at=now,
                    is_active=True, disappeared_at=None,
                ))
                if item.price_text:
                    session.add(PriceHistory(
                        external_id=item.external_id, price_text=item.price_text,
                        price_eur=item.price_eur, recorded_at=now,
                    ))
                new_items.append(item)
            else:
                old_price_text = row.price_text
                old_price_eur = row.price_eur
                # Never erase a previously parsed price because of one weak HTML response.
                if item.price_text is not None:
                    if old_price_text is None and item.price_text:
                        enriched_count += 1
                    if (old_price_text, old_price_eur) != (item.price_text, item.price_eur):
                        if old_price_text is not None or old_price_eur is not None:
                            session.add(PriceHistory(
                                external_id=external_id, price_text=old_price_text,
                                price_eur=old_price_eur, recorded_at=now - timedelta(microseconds=1),
                            ))
                        session.add(PriceHistory(
                            external_id=external_id, price_text=item.price_text,
                            price_eur=item.price_eur, recorded_at=now,
                        ))
                    row.price_text = item.price_text
                    row.price_eur = item.price_eur
                row.category_key = category_key
                row.category = category_name
                row.title = item.title
                row.posted_text = item.posted_text
                row.url = item.url
                row.last_seen_at = now
                row.is_active = True
                row.disappeared_at = None
        await session.commit()
        return new_items, len(unique) - len(new_items), enriched_count


def berlin_today_utc_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(BERLIN)
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


async def today_rows() -> list[Listing]:
    start_utc, end_utc = berlin_today_utc_bounds()
    async with SessionLocal() as session:
        result = await session.execute(select(Listing).where(
            Listing.first_seen_at >= start_utc, Listing.first_seen_at < end_utc,
        ))
        return list(result.scalars().all())


async def filtered_rows(user_id: int) -> tuple[UserSettings, list[Listing]]:
    s = await get_settings(user_id)
    rows = await today_rows()
    rows = base_filter(
        rows, period=s.period, price_filter=s.price_filter, clean_noise=s.clean_noise,
        include_words=s.include_words or "", exclude_words=s.exclude_words or "",
    )
    if s.smart_dedupe:
        rows = dedupe_rows(rows)
    if s.output_mode == "unique":
        rows = unique_rows(rows)
    return s, sort_rows(rows, s.sort_mode)


def _temp_csv(name: str) -> tuple[Path, csv.writer, object]:
    temp_dir = Path(tempfile.mkdtemp(prefix="kleinanzeigen_"))
    path = temp_dir / name
    f = path.open("w", encoding="utf-8-sig", newline="")
    return path, csv.writer(f, delimiter=";"), f


def _price_display(price_text: str | None, price_eur: int | None) -> str:
    if price_text:
        return price_text
    if price_eur is not None:
        return f"{price_eur} €"
    return "—"


def _berlin_text(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone(BERLIN).strftime("%d.%m.%Y %H:%M")


def write_listing_csv(rows: list[Listing], mode: str) -> Path:
    now = datetime.now(BERLIN)
    path, writer, f = _temp_csv(f"kleinanzeigen_{mode}_{now:%Y-%m-%d_%H-%M}.csv")
    try:
        writer.writerow(["Категория", "Название", "Цена", "Цена, €", "👁 Просмотры", "Дата публикации", "Ссылка"])
        for row in rows:
            writer.writerow([
                row.category, row.title, _price_display(row.price_text, row.price_eur),
                row.price_eur if row.price_eur is not None else "",
                row.view_count if row.view_count is not None else "",
                row.posted_text or "Сегодня", row.url,
            ])
    finally:
        f.close()
    return path


def write_frequent_csv(rows) -> Path:
    now = datetime.now(BERLIN)
    path, writer, f = _temp_csv(f"kleinanzeigen_chasto_publikuemye_{now:%Y-%m-%d_%H-%M}.csv")
    try:
        writer.writerow([
            "Категория", "Группа товара", "Пример названия", "Цена примера",
            "Публикаций", "Мин. цена, €", "Медиана, €", "Макс. цена, €",
            "Точность группы, %", "Последнее", "Ссылка-пример",
        ])
        for row in rows:
            writer.writerow([
                row.category, row.product_key, row.example_title, row.example_price_text or "—", row.count,
                row.min_price if row.min_price is not None else "",
                row.median_price if row.median_price is not None else "",
                row.max_price if row.max_price is not None else "",
                row.confidence, row.newest_posted, row.example_url,
            ])
    finally:
        f.close()
    return path


def write_market_csv(rows) -> Path:
    now = datetime.now(BERLIN)
    path, writer, f = _temp_csv(f"kleinanzeigen_nizhe_rynka_{now:%Y-%m-%d_%H-%M}.csv")
    try:
        writer.writerow([
            "Категория", "Название", "Цена", "Цена, €", "👁 Просмотры", "Медиана группы, €",
            "Ниже медианы, %", "Образцов", "Точность группы, %", "Дата", "Ссылка",
        ])
        for row in rows:
            writer.writerow([
                row.category, row.title, row.price_text or f"{row.price_eur} €", row.price_eur,
                getattr(row, "view_count", None) if getattr(row, "view_count", None) is not None else "",
                row.median_price, row.discount_pct, row.samples, row.confidence, row.posted_text, row.url,
            ])
    finally:
        f.close()
    return path


def write_disappearing_csv(rows) -> Path:
    now = datetime.now(BERLIN)
    path, writer, f = _temp_csv(f"kleinanzeigen_bystro_ischezayushchie_{now:%Y-%m-%d_%H-%M}.csv")
    try:
        writer.writerow([
            "Категория", "Название", "Цена", "Время жизни, мин",
            "Окно проверки, мин", "Точность", "Впервые замечено", "Обнаружено исчезновение", "Ссылка",
        ])
        for row in rows:
            writer.writerow([
                row.category, row.title, row.price_text or "—", row.lifespan_minutes,
                row.detection_gap_minutes, row.confidence,
                _berlin_text(row.first_seen_at), _berlin_text(row.disappeared_at), row.url,
            ])
    finally:
        f.close()
    return path


def write_price_drop_csv(rows) -> Path:
    now = datetime.now(BERLIN)
    path, writer, f = _temp_csv(f"kleinanzeigen_snizhenie_ceny_{now:%Y-%m-%d_%H-%M}.csv")
    try:
        writer.writerow([
            "Категория", "Название", "Старая цена, €", "Новая цена, €",
            "Снижение, €", "Снижение, %", "Зафиксировано", "Ссылка",
        ])
        for row in rows:
            writer.writerow([
                row.category, row.title, row.previous_price, row.current_price,
                row.drop_eur, row.drop_pct, _berlin_text(row.changed_at), row.url,
            ])
    finally:
        f.close()
    return path


async def histories_for(rows: list[Listing]) -> list[PriceHistory]:
    ids = [row.external_id for row in rows]
    if not ids:
        return []
    async with SessionLocal() as session:
        result = await session.execute(
            select(PriceHistory).where(PriceHistory.external_id.in_(ids)).order_by(PriceHistory.recorded_at.asc())
        )
        return list(result.scalars().all())


async def refresh_availability(rows: list[Listing]) -> tuple[int, int, int]:
    """Check a bounded batch of tracked public ad links for availability.

    Returns checked, newly_disappeared, unknown. This is intentionally bounded
    and low-concurrency so the analytics mode does not hammer the site.
    """
    candidates = [r for r in rows if r.is_active and r.url][:AVAILABILITY_CHECK_LIMIT]
    if not candidates:
        return 0, 0, 0

    parser = KleinanzeigenParser()
    sem = asyncio.Semaphore(AVAILABILITY_CONCURRENCY)

    async def check(row: Listing):
        async with sem:
            result = await parser.check_listing_active(row.url)
            return row.external_id, result

    try:
        results = await asyncio.gather(*(check(row) for row in candidates))
    finally:
        await parser.close()

    disappeared_ids = [external_id for external_id, active in results if active is False]
    unknown = sum(1 for _, active in results if active is None)
    if disappeared_ids:
        now = datetime.utcnow()
        async with SessionLocal() as session:
            result = await session.execute(select(Listing).where(Listing.external_id.in_(disappeared_ids)))
            found = list(result.scalars().all())
            for row in found:
                if row.is_active:
                    row.is_active = False
                    row.disappeared_at = now
            await session.commit()
    return len(candidates), len(disappeared_ids), unknown


async def enrich_page_view_counts(
    parser: KleinanzeigenParser,
    items: list[ParsedListing],
    live: CategoryLiveProgress | None = None,
) -> tuple[int, int, int]:
    """Fetch public view counters as part of the category-page pipeline.

    Only missing/stale counters are opened. The same Playwright browser is reused
    by the category parser, and the passive s-vac-inc-get response is preferred.
    Returns (requested, updated, failed).
    """
    if not items:
        return 0, 0, 0

    unique = {item.external_id: item for item in items if item.url}
    if not unique:
        return 0, 0, 0

    cutoff = datetime.utcnow() - timedelta(seconds=VIEW_COUNT_CACHE_TTL_SECONDS)
    async with SessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.external_id.in_(list(unique))))
        rows = {row.external_id: row for row in result.scalars().all()}
        targets = [
            unique[eid] for eid, row in rows.items()
            if row.views_checked_at is None or row.views_checked_at < cutoff
        ]

    if not targets:
        if live is not None:
            live.views_ready += len(unique)
        return 0, 0, 0

    results = await parser.fetch_public_view_counts(
        [item.url for item in targets],
        concurrency=VIEW_COUNT_CONCURRENCY,
    )

    now = datetime.utcnow()
    updated = 0
    failed = 0
    url_to_id = {item.url: item.external_id for item in targets}
    async with db_write_lock:
        async with SessionLocal() as session:
            db_result = await session.execute(select(Listing).where(Listing.external_id.in_([item.external_id for item in targets])))
            db_rows = {row.external_id: row for row in db_result.scalars().all()}
            for url, vr in results.items():
                external_id = url_to_id.get(url)
                row = db_rows.get(external_id) if external_id else None
                if row is None:
                    continue
                if vr.views is None:
                    failed += 1
                    continue
                old = row.view_count
                row.view_count = int(vr.views)
                row.views_checked_at = now
                if old != row.view_count:
                    session.add(ViewHistory(
                        external_id=row.external_id,
                        view_count=row.view_count,
                        recorded_at=now,
                    ))
                updated += 1
            await session.commit()

    if live is not None:
        live.views_ready += updated + max(0, len(unique) - len(targets))
        live.views_failed += failed
    return len(targets), updated, failed


async def refresh_view_counts(rows: list[Listing], message: Message | BotChatAdapter | None = None) -> tuple[int, int, int]:
    """Refresh missing/stale public view counters and persist them.

    Returns (requested, updated, failed). Recent counters are reused from the DB.
    """
    if not rows:
        return 0, 0, 0

    cutoff = datetime.utcnow() - timedelta(seconds=VIEW_COUNT_CACHE_TTL_SECONDS)
    targets = [
        row for row in rows
        if row.url and (row.views_checked_at is None or row.views_checked_at < cutoff)
    ]
    if not targets:
        return 0, 0, 0

    status = None
    if message is not None:
        try:
            status = await message.answer(
                f"👁 Собираю просмотры для <b>{len(targets)}</b> объявлений…\n"
                f"⚡ Прямой счётчик + browser fallback · кэш {max(1, VIEW_COUNT_CACHE_TTL_SECONDS // 60)} мин.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            status = None

    async def progress_cb(done: int, total: int):
        if status is not None and hasattr(status, "edit_text"):
            try:
                pct = round(done / total * 100) if total else 100
                await status.edit_text(
                    f"👁 Собираю просмотры… <b>{done}/{total}</b> ({pct}%)\n"
                    f"⚡ Прямой счётчик + browser fallback · кэш {max(1, VIEW_COUNT_CACHE_TTL_SECONDS // 60)} мин.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    parser = KleinanzeigenParser()
    try:
        results = await parser.fetch_public_view_counts(
            [row.url for row in targets],
            concurrency=VIEW_COUNT_CONCURRENCY,
            progress_cb=progress_cb,
        )
    finally:
        await parser.close()

    now = datetime.utcnow()
    updated = 0
    failed = 0
    by_id = {row.external_id: row for row in targets}
    url_to_id = {row.url: row.external_id for row in targets}
    async with db_write_lock:
        async with SessionLocal() as session:
            result = await session.execute(select(Listing).where(Listing.external_id.in_(list(by_id))))
            db_rows = {row.external_id: row for row in result.scalars().all()}
            for url, vr in results.items():
                external_id = url_to_id.get(url)
                row = db_rows.get(external_id) if external_id else None
                if row is None:
                    continue
                if vr.views is None:
                    failed += 1
                    continue
                old = row.view_count
                row.view_count = int(vr.views)
                row.views_checked_at = now
                if old != row.view_count:
                    session.add(ViewHistory(
                        external_id=row.external_id,
                        view_count=row.view_count,
                        recorded_at=now,
                    ))
                updated += 1
            await session.commit()

    # Update the already-loaded ORM objects so the CSV can be written without a reload.
    for row in targets:
        vr = results.get(row.url)
        if vr and vr.views is not None:
            row.view_count = int(vr.views)
            row.views_checked_at = now

    if status is not None and hasattr(status, "edit_text"):
        try:
            await status.edit_text(
                f"👁 Просмотры готовы: <b>{updated}</b> · не удалось: <b>{failed}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    return len(targets), updated, failed


async def send_smart_export(
    message: Message | BotChatAdapter,
    user_id: int,
    selected_count: int,
    *,
    category_keys_override: set[str] | None = None,
    rows_override: list[Listing] | None = None,
) -> int:
    s = await get_settings(user_id)
    mode = s.output_mode
    all_rows = list(rows_override) if rows_override is not None else await today_rows()
    selected_keys = category_keys_override if category_keys_override is not None else await get_selected(user_id)
    if selected_keys:
        all_rows = [row for row in all_rows if row.category_key in selected_keys]
    raw_base = base_filter(
        all_rows, period=s.period, price_filter=s.price_filter, clean_noise=s.clean_noise,
        include_words=s.include_words or "", exclude_words=s.exclude_words or "",
    )

    # Frequency intentionally sees all distinct IDs; otherwise smart de-duplication
    # would hide the very repetitions this mode is meant to measure.
    base = raw_base
    if s.smart_dedupe and mode != "frequent":
        base = dedupe_rows(base)

    # v2.7.0: view counters are collected during the category scan itself.
    # Export is intentionally read-only so the result file is available immediately.

    if mode == "frequent":
        result = frequent_rows(base, min_count=3)
        if not result:
            await message.answer("🔥 Пока нет групп минимум с 3 публикациями по текущим фильтрам.", reply_markup=main_keyboard(selected_count))
            return 0
        path = write_frequent_csv(result)
        caption = f"🔥 Часто публикуемые группы: {len(result)}"

    elif mode == "below_market":
        result = below_market_rows(base)
        if not result:
            await message.answer("💰 Нужны минимум 5 цен в одной уверенной группе; сейчас нет позиций ≥20% ниже медианы похожих объявлений.", reply_markup=main_keyboard(selected_count))
            return 0
        path = write_market_csv(result)
        caption = f"💰 Потенциально ниже рынка: {len(result)}"

    elif mode == "fast_disappearing":
        status = await message.answer(
            f"⚡ Проверяю доступность до <b>{AVAILABILITY_CHECK_LIMIT}</b> сегодняшних объявлений…",
            parse_mode=ParseMode.HTML,
        )
        checked, newly_disappeared, unknown = await refresh_availability(base)
        # Reload rows because availability status may have changed.
        all_rows = await today_rows()
        refreshed = base_filter(
            all_rows, period=s.period, price_filter=s.price_filter, clean_noise=s.clean_noise,
            include_words=s.include_words or "", exclude_words=s.exclude_words or "",
        )
        if s.smart_dedupe:
            refreshed = dedupe_rows(refreshed)
        result = disappearing_rows(refreshed, max_lifespan_hours=12)
        await status.edit_text(
            f"⚡ Проверено: <b>{checked}</b> · новых исчезнувших: <b>{newly_disappeared}</b> · неопределённых: <b>{unknown}</b>",
            parse_mode=ParseMode.HTML,
        )
        if not result:
            await message.answer(
                "⚡ Пока нет объявлений, исчезнувших примерно за ≤12 часов. Точность растёт при регулярных проверках в течение дня.",
                reply_markup=main_keyboard(selected_count),
            )
            return 0
        path = write_disappearing_csv(result)
        caption = f"⚡ Быстро исчезающие: {len(result)}"

    elif mode == "price_drop":
        histories = await histories_for(base)
        result = price_drop_rows(base, histories, min_drop_pct=5, min_drop_eur=5)
        if not result:
            await message.answer(
                "📉 Пока нет подтверждённых снижений минимум на 5 € и 5%. Нужен повторный парсинг после изменения цены объявления.",
                reply_markup=main_keyboard(selected_count),
            )
            return 0
        path = write_price_drop_csv(result)
        caption = f"📉 Снижение цены: {len(result)}"

    else:
        result = unique_rows(base) if mode == "unique" else sort_rows(base, s.sort_mode)
        if mode == "unique":
            result = sort_rows(result, s.sort_mode)
        if not result:
            await message.answer("📦 По текущим фильтрам ничего не найдено.", reply_markup=main_keyboard(selected_count))
            return 0
        path = write_listing_csv(result, mode)
        caption = f"📦 {MODE_LABELS.get(mode, mode)}: {len(result)}"

    try:
        await message.answer_document(FSInputFile(path), caption=caption, reply_markup=main_keyboard(selected_count))
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    return len(result)


def settings_text(s: UserSettings) -> str:
    include = html.escape(s.include_words) if s.include_words else "—"
    exclude = html.escape(s.exclude_words) if s.exclude_words else "—"
    return (
        "<b>⚙️ Настройки парсинга</b>\n\n"
        "Парсер всё равно сохраняет полный массив. Эти настройки меняют только результат/выгрузку.\n\n"
        f"Режим: <b>{MODE_LABELS.get(s.output_mode, s.output_mode)}</b>\n"
        f"Умные дубли: <b>{'ВКЛ' if s.smart_dedupe else 'ВЫКЛ'}</b>\n"
        f"Чистить услуги/поиск: <b>{'ВКЛ' if s.clean_noise else 'ВЫКЛ'}</b>\n"
        f"Период: <b>{PERIOD_LABELS.get(s.period, s.period)}</b>\n"
        f"Цена: <b>{PRICE_LABELS.get(s.price_filter, s.price_filter)}</b>\n"
        f"Сортировка: <b>{SORT_LABELS.get(s.sort_mode, s.sort_mode)}</b>\n"
        f"Последняя глубина запуска: <b>{getattr(s, 'page_limit', 100)} страниц</b>\n"
        f"Ключевые слова: <b>{include}</b>\n"
        f"Исключить: <b>{exclude}</b>\n\n"
        "<i>v2.6.6 сохраняет Smart Analytics/Fast Incremental и добавляет пользовательский живой прогресс; внутренняя очередь, кэш и совместные сканы скрыты от пользователей. "
        "Нажми «ℹ️ Как работают режимы», чтобы увидеть текущие критерии.</i>"
    )


async def stats_text() -> str:
    start_utc, end_utc = berlin_today_utc_bounds()
    day_key = berlin_date_key()
    async with SessionLocal() as session:
        total = (await session.execute(select(func.count(Listing.id)))).scalar_one()
        today = (await session.execute(select(func.count(Listing.id)).where(
            Listing.first_seen_at >= start_utc, Listing.first_seen_at < end_utc,
        ))).scalar_one()
        priced = (await session.execute(select(func.count(Listing.id)).where(
            Listing.first_seen_at >= start_utc, Listing.first_seen_at < end_utc, Listing.price_text.is_not(None),
        ))).scalar_one()
        viewed = (await session.execute(select(func.count(Listing.id)).where(
            Listing.first_seen_at >= start_utc, Listing.first_seen_at < end_utc, Listing.view_count.is_not(None),
        ))).scalar_one()
        drops = (await session.execute(select(func.count(PriceHistory.id)))).scalar_one()
        runs = (await session.execute(select(func.count(ParserRun.id)).where(
            ParserRun.started_at >= start_utc, ParserRun.started_at < end_utc, ParserRun.success.is_(True),
        ))).scalar_one()
        fast_runs = (await session.execute(select(func.count(ParserRun.id)).where(
            ParserRun.started_at >= start_utc, ParserRun.started_at < end_utc,
            ParserRun.success.is_(True), ParserRun.mode == "fast",
        ))).scalar_one()
        pages = (await session.execute(select(func.coalesce(func.sum(ParserRun.pages_scanned), 0)).where(
            ParserRun.started_at >= start_utc, ParserRun.started_at < end_utc, ParserRun.success.is_(True),
        ))).scalar_one()
        scan_new = (await session.execute(select(func.coalesce(func.sum(ParserRun.new_count), 0)).where(
            ParserRun.started_at >= start_utc, ParserRun.started_at < end_utc, ParserRun.success.is_(True),
        ))).scalar_one()
        fast_ready = (await session.execute(select(func.count(CategoryScanState.category_key)).where(
            CategoryScanState.scan_date == day_key, CategoryScanState.day_seed_complete.is_(True),
        ))).scalar_one()
    storage = "PostgreSQL / DATABASE_URL" if USING_PERSISTENT_DATABASE else "SQLite"
    warning = "" if USING_PERSISTENT_DATABASE else "\n\n⚠️ На Railway SQLite может потеряться после redeploy/restart."
    coverage = round(priced / today * 100) if today else 0
    view_coverage = round(viewed / today * 100) if today else 0
    async with job_guard:
        running_jobs_count = sum(1 for j in active_jobs.values() if j.state == "running")
        queued_jobs_count = sum(1 for j in active_jobs.values() if j.state == "queued" and not j.cancel_requested)
        inflight_categories_count = len(category_inflight)
    return (
        f"<b>📊 База и парсинг</b>\n\n"
        f"Сегодня собрано: <b>{today}</b>\n"
        f"С ценой: <b>{priced}</b> ({coverage}%)\n"
        f"С просмотрами: <b>{viewed}</b> ({view_coverage}%)\n"
        f"Всего сохранено: <b>{total}</b>\n"
        f"Записей истории цен: <b>{drops}</b>\n\n"
        f"<b>⚡ v2.6 сегодня</b>\n"
        f"Запусков категорий: <b>{runs}</b>\n"
        f"Быстрых запусков: <b>{fast_runs}</b>\n"
        f"Категорий готовы к fast-mode: <b>{fast_ready}</b>\n"
        f"Пройдено страниц: <b>{pages}</b>\n"
        f"Найдено новых за запуски: <b>{scan_new}</b>\n\n"
        f"База: <b>{storage}</b>{warning}"
    )


@dataclass
class ScanResult:
    new_count: int
    pages_scanned: int
    today_seen: int
    known_count: int
    enriched_count: int
    hit_limit: bool
    reason: str
    mode: str
    avoided_pages: int = 0


@dataclass
class CategoryDispatchResult:
    source: str  # scan | shared | cache
    result: ScanResult | None = None
    cache_age_seconds: int = 0


@dataclass
class ScanJob:
    job_id: str
    user_id: int
    chat_id: int
    status_message_id: int
    category_keys: list[str]
    created_at: datetime
    state: str = "queued"
    cancel_requested: bool = False
    worker_id: int | None = None
    current_category: str = ""
    completed_categories: int = 0
    total_new: int = 0
    total_pages: int = 0
    total_avoided: int = 0
    cache_hits: int = 0
    shared_hits: int = 0
    scanned_categories: int = 0
    fast_categories: int = 0
    full_categories: int = 0
    warnings: list[str] | None = None
    last_status_update: float = 0.0
    current_category_key: str = ""
    current_category_index: int = 0
    started_running_monotonic: float = 0.0
    page_limit: int = 100
    current_progress_key: str = ""
    scan_id: int | None = None


@dataclass
class CategoryLiveProgress:
    category_key: str
    category_name: str
    mode: str
    page: int = 0
    today_seen: int = 0
    new_count: int = 0
    known_count: int = 0
    views_ready: int = 0
    views_failed: int = 0
    estimated_pages: int = 10
    started_monotonic: float = 0.0
    page_limit: int = 100


category_live_progress: dict[str, CategoryLiveProgress] = {}


scan_queue: asyncio.Queue[ScanJob] = asyncio.Queue()
active_jobs: dict[int, ScanJob] = {}
queued_job_ids: list[str] = []
job_guard = asyncio.Lock()
category_inflight: dict[str, asyncio.Task[ScanResult]] = {}
category_inflight_guard = asyncio.Lock()
db_write_lock = asyncio.Lock()


def berlin_date_key() -> str:
    return datetime.now(BERLIN).date().isoformat()


async def get_category_scan_state(category_key: str) -> CategoryScanState | None:
    async with SessionLocal() as session:
        return await session.get(CategoryScanState, category_key)


async def save_category_scan_state(
    category_key: str,
    *,
    mode: str,
    pages_scanned: int,
    new_count: int,
    today_seen: int,
    reason: str,
    head_ids: list[str],
    seed_complete: bool,
    seed_capped: bool,
) -> CategoryScanState:
    day_key = berlin_date_key()
    async with SessionLocal() as session:
        state = await session.get(CategoryScanState, category_key)
        if state is None:
            state = CategoryScanState(category_key=category_key, scan_date=day_key)
            session.add(state)
        new_day = state.scan_date != day_key
        if new_day:
            state.scan_date = day_key
            state.total_runs = 0
            state.day_seed_complete = False
            state.day_seed_capped = False
            state.day_full_pages = 0
            state.head_ids = ""

        if head_ids:
            state.head_ids = ",".join(head_ids[:INCREMENTAL_HEAD_SIZE])
        state.last_scan_at = datetime.utcnow()
        state.last_mode = mode
        state.last_pages = pages_scanned
        state.last_new = new_count
        state.last_today_seen = today_seen
        state.last_stop_reason = reason[:255]
        state.total_runs = (state.total_runs or 0) + 1
        if mode == "full":
            # Keep the deepest seeded window for the day. A 25-page seed enables
            # later 25-page fast scans, while a later 100-page request can deepen it.
            state.day_full_pages = max(state.day_full_pages or 0, pages_scanned)
            if seed_complete:
                state.day_seed_complete = True
                state.day_seed_capped = False
            elif seed_capped and not state.day_seed_complete:
                state.day_seed_capped = True
        await session.commit()
        await session.refresh(state)
        return state


async def record_parser_run(
    user_id: int,
    cat,
    result: ScanResult,
    started_at: datetime,
    *,
    success: bool = True,
    error_text: str | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(ParserRun(
            user_id=user_id,
            category_key=cat.key,
            category_name=cat.name,
            mode=result.mode,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            pages_scanned=result.pages_scanned,
            today_seen=result.today_seen,
            new_count=result.new_count,
            known_count=result.known_count,
            enriched_count=result.enriched_count,
            stop_reason=result.reason[:255],
            success=success,
            error_text=(error_text[:1000] if error_text else None),
        ))
        await session.commit()


async def scan_one_category(parser: KleinanzeigenParser, cat, user_id: int, page_limit: int) -> ScanResult:
    """Scan one category.

    v2.5 uses two modes:
    - full: once per Berlin day/category, walk the current-day feed until Heute ends;
    - fast: later scans start at page 1 and stop after reaching the previous head
      checkpoint plus a safety overlap, or after several highly-known pages.

    The fast mode never skips the first pages: this keeps it correct when many new
    listings arrived since the previous run. It only avoids re-reading the old tail.
    """
    scan_limit = max(1, min(MAX_PAGES_PER_CATEGORY, int(page_limit)))
    progress_key = f"{cat.key}:{scan_limit}"
    state = await get_category_scan_state(cat.key)
    day_key = berlin_date_key()
    can_fast = bool(
        state
        and state.scan_date == day_key
        and (state.day_seed_complete or (state.day_full_pages or 0) >= scan_limit)
    )
    mode = "fast" if can_fast else "full"
    previous_heads = {
        x for x in ((state.head_ids if state else "") or "").split(",") if x
    }
    baseline_pages = (state.day_full_pages or 0) if state and state.scan_date == day_key else 0
    # A rough page estimate is used only for the user-facing progress/ETA. It is
    # deliberately conservative and is recalibrated while the scan runs.
    if state and (state.day_full_pages or state.last_pages):
        historic_pages = int(state.day_full_pages or state.last_pages or 10)
    else:
        historic_pages = 10
    estimated_pages = scan_limit
    if mode == "fast":
        estimated_pages = max(INCREMENTAL_MIN_PAGES, min(scan_limit, 8, int(state.last_pages or 3) if state else 3))
    category_live_progress[progress_key] = CategoryLiveProgress(
        category_key=cat.key,
        category_name=cat.name,
        mode=mode,
        estimated_pages=estimated_pages,
        started_monotonic=time.monotonic(),
        page_limit=scan_limit,
    )

    new_count = 0
    today_seen = 0
    pages_scanned = 0
    known_total = 0
    enriched_total = 0
    empty_today_pages = 0
    consecutive_known_pages = 0
    first_page_head_ids: list[str] = []
    checkpoint_seen_page: int | None = None
    started_at = datetime.utcnow()
    reason = ""
    hit_limit = False

    try:
        for page in range(1, scan_limit + 1):
            try:
                items = await parser.parse_category_page(page_url(cat.url, page))
            except TemporaryAccessError as exc:
                # Keep everything already collected. Do not mark the daily seed as
                # complete, so a later run can continue/deepen the category.
                reason = f"временный лимит Kleinanzeigen (HTTP {exc.status_code}); сохранён частичный результат"
                log.warning("category=%s page=%s stopped after temporary refusal: %s", cat.name, page, exc)
                break
            pages_scanned = page
            if not items:
                reason = "конец выдачи"
                break

            today_items = [item for item in items if is_today_text(item.posted_text)]
            today_seen += len(today_items)
            live = category_live_progress.get(progress_key)
            if live is not None:
                live.page = page
                live.today_seen = today_seen
                # If a first/full scan is larger than our historical estimate, expand
                # the estimate instead of showing a fake 100% too early.
                if page >= live.estimated_pages:
                    live.estimated_pages = min(scan_limit, page + (2 if mode == "fast" else 5))

            if page == 1 and today_items:
                first_page_head_ids = [item.external_id for item in today_items[:INCREMENTAL_HEAD_SIZE]]

            if today_items:
                empty_today_pages = 0
                page_ids = {item.external_id for item in today_items}

                async with db_write_lock:
                    new_items, known_count, enriched_count = await upsert_page_items(cat.key, cat.name, today_items)

                # v2.7.0: collect public view counters immediately for this page.
                # Recent counters are served from the DB cache; stale/missing ones
                # are opened through the same lightweight Playwright pool.
                live = category_live_progress.get(progress_key)
                views_requested, views_updated, views_failed = await enrich_page_view_counts(
                    parser, today_items, live
                )
                page_new = len(new_items)
                new_count += page_new
                known_total += known_count
                enriched_total += enriched_count
                live = category_live_progress.get(progress_key)
                if live is not None:
                    live.new_count = new_count
                    live.known_count = known_total

                known_ratio = known_count / max(1, len(today_items))
                # A promoted/repeated old ad can appear near the top. Only treat a
                # previous head ID as the boundary when the page is already mostly
                # known; this avoids an early checkpoint on a page full of fresh ads.
                if (
                    mode == "fast"
                    and checkpoint_seen_page is None
                    and previous_heads.intersection(page_ids)
                    and known_ratio >= 0.50
                ):
                    checkpoint_seen_page = page

                page_is_known_tail = (
                    page_new == 0
                    and enriched_count == 0
                    and known_ratio >= INCREMENTAL_MIN_KNOWN_RATIO
                )
                consecutive_known_pages = consecutive_known_pages + 1 if page_is_known_tail else 0

                log.info(
                    "category=%s mode=%s page=%s total=%s today=%s new=%s known=%s known_ratio=%.2f "
                    "prices_backfilled=%s views_requested=%s views_updated=%s views_failed=%s "
                    "checkpoint_page=%s known_pages=%s",
                    cat.name, mode, page, len(items), len(today_items), page_new, known_count,
                    known_ratio, enriched_count, views_requested, views_updated, views_failed,
                    checkpoint_seen_page, consecutive_known_pages,
                )

                if mode == "fast" and page >= INCREMENTAL_MIN_PAGES:
                    # Preferred stop: we crossed an ID that was near the top of the
                    # previous run, then scanned a safety overlap into the known tail.
                    if (
                        checkpoint_seen_page is not None
                        and page >= checkpoint_seen_page + INCREMENTAL_OVERLAP_PAGES
                        and consecutive_known_pages >= 1
                    ):
                        reason = "быстрый стоп: достигнут прошлый чекпоинт"
                        break

                    # Fallback if the old head ad was deleted/reordered: several pages
                    # dominated by already-known IDs are enough evidence that the fresh
                    # prefix has ended.
                    if consecutive_known_pages >= INCREMENTAL_STOP_AFTER_KNOWN_PAGES:
                        reason = "быстрый стоп: пошёл уже известный хвост"
                        break
            else:
                empty_today_pages += 1
                consecutive_known_pages = 0
                log.info(
                    "category=%s mode=%s page=%s total=%s today=0 empty_today_pages=%s",
                    cat.name, mode, page, len(items), empty_today_pages,
                )
                if empty_today_pages >= STOP_AFTER_EMPTY_TODAY_PAGES:
                    reason = "закончились объявления Heute"
                    break

            if PAGE_DELAY_SECONDS and page < scan_limit:
                await asyncio.sleep(PAGE_DELAY_SECONDS)
        else:
            hit_limit = True
            reason = f"лимит {scan_limit} страниц достигнут"

        if not reason:
            reason = "завершено"

        # v2.6.3 treats the configured 100-page window as a complete seed.
        # Very large categories intentionally keep only the newest window; after
        # that first capped scan, later runs may use fast incremental mode instead
        # of re-reading all 100 pages every time.
        interrupted = reason.startswith("временный лимит Kleinanzeigen")
        seed_complete = (mode == "full" and not hit_limit and not interrupted)
        seed_capped = (mode == "full" and hit_limit and not interrupted)
        saved = await save_category_scan_state(
            cat.key,
            mode=mode,
            pages_scanned=pages_scanned,
            new_count=new_count,
            today_seen=today_seen,
            reason=reason,
            head_ids=first_page_head_ids,
            seed_complete=seed_complete,
            seed_capped=seed_capped,
        )
        avoided_pages = max(0, (saved.day_full_pages or baseline_pages or 0) - pages_scanned) if mode == "fast" else 0
        result = ScanResult(
            new_count=new_count,
            pages_scanned=pages_scanned,
            today_seen=today_seen,
            known_count=known_total,
            enriched_count=enriched_total,
            hit_limit=hit_limit,
            reason=reason,
            mode=mode,
            avoided_pages=avoided_pages,
        )
        await record_parser_run(user_id, cat, result, started_at)
        return result
    except Exception as exc:
        failed = ScanResult(
            new_count=new_count,
            pages_scanned=pages_scanned,
            today_seen=today_seen,
            known_count=known_total,
            enriched_count=enriched_total,
            hit_limit=False,
            reason="ошибка",
            mode=mode,
            avoided_pages=0,
        )
        try:
            await record_parser_run(user_id, cat, failed, started_at, success=False, error_text=str(exc))
        except Exception:
            log.exception("Could not record failed parser run")
        raise


def job_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить парсинг", callback_data=f"cancel_scan:{job_id}")],
    ])


async def fresh_category_cache_age(category_key: str, page_limit: int) -> int | None:
    """Return cache age in seconds when a category can be safely reused."""
    if CATEGORY_CACHE_TTL_SECONDS <= 0:
        return None
    state = await get_category_scan_state(category_key)
    if not state or not state.last_scan_at:
        return None
    if state.scan_date != berlin_date_key():
        return None
    requested = max(1, min(MAX_PAGES_PER_CATEGORY, int(page_limit)))
    if not (state.day_seed_complete or (state.day_full_pages or 0) >= requested):
        return None
    age = max(0, int((datetime.utcnow() - state.last_scan_at).total_seconds()))
    if age <= CATEGORY_CACHE_TTL_SECONDS:
        return age
    return None


async def _scan_category_task(cat, user_id: int, page_limit: int) -> ScanResult:
    parser = KleinanzeigenParser()
    try:
        return await scan_one_category(parser, cat, user_id, page_limit)
    finally:
        await parser.close()


async def dispatch_category(cat, user_id: int, page_limit: int) -> CategoryDispatchResult:
    """Use a fresh cache, join an in-flight scan, or start exactly one scan.

    This is the core v2.6 de-duplication layer: 15 users requesting Konsolen at
    the same moment still cause only one network scan of Konsolen.
    """
    cache_age = await fresh_category_cache_age(cat.key, page_limit)
    if cache_age is not None:
        return CategoryDispatchResult(source="cache", cache_age_seconds=cache_age)

    inflight_key = f"{cat.key}:{max(1, min(MAX_PAGES_PER_CATEGORY, int(page_limit)))}"
    async with category_inflight_guard:
        task = category_inflight.get(inflight_key)
        if task is None:
            task = asyncio.create_task(
                _scan_category_task(cat, user_id, page_limit),
                name=f"category-scan:{inflight_key}",
            )
            category_inflight[inflight_key] = task
            source = "scan"
        else:
            source = "shared"

    try:
        result = await asyncio.shield(task)
        return CategoryDispatchResult(source=source, result=result)
    finally:
        if task.done():
            async with category_inflight_guard:
                if category_inflight.get(inflight_key) is task:
                    category_inflight.pop(inflight_key, None)
            category_live_progress.pop(inflight_key, None)


async def queue_status_text(user_id: int) -> str:
    async with job_guard:
        running = [j for j in active_jobs.values() if j.state == "running"]
        queued = [j for j in active_jobs.values() if j.state == "queued" and not j.cancel_requested]
        mine = active_jobs.get(user_id)
        position = None
        if mine and mine.state == "queued" and mine.job_id in queued_job_ids:
            position = queued_job_ids.index(mine.job_id) + 1

    lines = [
        "<b>📥 Очередь парсинга</b>",
        "",
        f"Воркеров: <b>{MAX_CONCURRENT_JOBS}</b>",
        f"Лимит очереди: <b>{MAX_QUEUE_SIZE}</b>",
        f"Сейчас выполняется: <b>{len(running)}</b>",
        f"Ждут в очереди: <b>{len(queued)}</b>",
        f"Кэш категории: <b>{CATEGORY_CACHE_TTL_SECONDS // 60} мин.</b>" if CATEGORY_CACHE_TTL_SECONDS else "Кэш категории: <b>выключен</b>",
    ]
    if mine:
        lines += ["", "<b>Твоя задача</b>"]
        if mine.state == "queued":
            lines.append(f"⏳ В очереди" + (f", позиция примерно <b>{position}</b>" if position else ""))
        elif mine.state == "running":
            lines.append(f"⚙️ Выполняется воркером <b>#{mine.worker_id}</b>")
            if mine.current_category:
                lines.append(f"Сейчас: <b>{html.escape(mine.current_category)}</b>")
            lines.append(f"Готово категорий: <b>{mine.completed_categories}/{len(mine.category_keys)}</b>")
        elif mine.cancel_requested:
            lines.append("❌ Ожидает отмены")
    else:
        lines += ["", "У тебя сейчас нет активного запуска."]
    return "\n".join(lines)


def _human_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "считаю…"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} сек"
    minutes, secs = divmod(seconds, 60)
    if minutes < 10 and secs >= 30:
        minutes += 1
    return f"{minutes} мин"


def _human_eta(seconds: float | int | None) -> str:
    """Conservative ETA: avoid misleading 10–15 second guesses."""
    if seconds is None:
        return "считаю…"
    seconds = max(0, float(seconds))
    if seconds < 45:
        return "меньше 1 мин"
    import math
    return f"≈ {max(1, math.ceil(seconds / 60))} мин"


def _base_category_eta_seconds(page_limit: int) -> int:
    limit = min(PAGE_LIMIT_CHOICES, key=lambda x: abs(x - int(page_limit)))
    return PAGE_LIMIT_BASE_ETA_SECONDS[limit]


def _progress_bar(percent: int) -> str:
    percent = max(0, min(100, percent))
    filled = min(10, percent // 10)
    return "█" * filled + "░" * (10 - filled)


def render_user_job_status(job: ScanJob) -> str:
    total = max(1, len(job.category_keys))
    base_category_eta = _base_category_eta_seconds(job.page_limit)

    if job.state == "queued":
        waited = max(0, int((datetime.utcnow() - job.created_at).total_seconds()))
        total_estimate = base_category_eta * total
        return (
            "⏳ <b>Подготавливаю парсинг…</b>\n\n"
            f"Выбрано категорий: <b>{total}</b>\n"
            f"Глубина: <b>{job.page_limit} страниц на категорию</b>\n"
            f"Ориентировочное время: <b>{_human_eta(total_estimate)}</b>\n"
            f"Подготовка: <b>{waited} сек</b>\n\n"
            "Статус обновляется автоматически."
        )

    live = category_live_progress.get(job.current_progress_key) if job.current_progress_key else None
    current_fraction = 0.0
    current_page = 0
    current_today = 0
    live_new = 0
    live_views_ready = 0
    live_views_failed = 0
    current_eta = float(base_category_eta)

    if live is not None:
        current_page = live.page
        current_today = live.today_seen
        live_new = live.new_count
        live_views_ready = live.views_ready
        live_views_failed = live.views_failed
        target_pages = max(1, live.estimated_pages if live.mode == "fast" else job.page_limit)
        current_fraction = min(0.95, current_page / target_pages)

        # Start conservatively, then blend in the actual observed page rate.
        base_remaining = base_category_eta * max(0.0, 1.0 - current_fraction)
        current_eta = base_remaining
        if current_page >= 3 and live.started_monotonic:
            cat_elapsed = max(1.0, time.monotonic() - live.started_monotonic)
            seconds_per_page = cat_elapsed / max(1, current_page)
            observed_remaining = seconds_per_page * max(0, target_pages - current_page)
            current_eta = 0.55 * base_remaining + 0.45 * observed_remaining

    completed_units = min(total, job.completed_categories + current_fraction)
    fraction = max(0.0, min(1.0, completed_units / total))
    percent = int(fraction * 100)
    if job.completed_categories >= total:
        percent = 100

    elapsed = 0
    if job.started_running_monotonic:
        elapsed = max(0, int(time.monotonic() - job.started_running_monotonic))

    remaining_after_current = max(0, total - job.completed_categories - (1 if job.current_category else 0))
    eta = current_eta + remaining_after_current * base_category_eta
    if job.completed_categories >= total:
        eta = 0

    category_line = html.escape(job.current_category) if job.current_category else "Подготовка…"
    page_line = f"\nСтраница: <b>{current_page} / {job.page_limit}</b>" if current_page else ""
    today_line = f"\nОбъявлений обработано в категории: <b>{current_today}</b>" if current_today else ""
    views_line = ""
    if current_today:
        views_line = f"\n👁 Просмотры готовы: <b>{live_views_ready}/{current_today}</b>"
        if live_views_failed:
            views_line += f" · ошибок: <b>{live_views_failed}</b>"
    visible_new = job.total_new + live_new

    return (
        "🔄 <b>Парсинг идёт</b>\n\n"
        f"{_progress_bar(percent)} <b>{percent}%</b>\n"
        f"Категории: <b>{job.completed_categories}/{total}</b> готово\n"
        f"Глубина: <b>{job.page_limit} страниц</b>\n"
        f"Сейчас: <b>{category_line}</b>{page_line}{today_line}{views_line}\n\n"
        f"🆕 Найдено новых: <b>{visible_new}</b>\n"
        f"⏱ Прошло: <b>{_human_duration(elapsed)}</b>\n"
        f"⌛ Осталось примерно: <b>{_human_eta(eta)}</b>\n\n"
        "Прогресс обновляется автоматически."
    )


async def progress_ticker(bot: Bot) -> None:
    """Continuously refresh user-facing progress without exposing internal scheduling."""
    while True:
        await asyncio.sleep(max(2.0, STATUS_UPDATE_INTERVAL_SECONDS))
        async with job_guard:
            jobs = list(active_jobs.values())
        for job in jobs:
            if job.state not in {"queued", "running"} or job.cancel_requested:
                continue
            try:
                await edit_job_status(bot, job, render_user_job_status(job))
            except Exception:
                log.debug("Could not refresh live progress for job=%s", job.job_id, exc_info=True)


async def edit_job_status(bot: Bot, job: ScanJob, text: str, *, force: bool = False) -> None:
    now = time.monotonic()
    if not force and now - job.last_status_update < STATUS_UPDATE_INTERVAL_SECONDS:
        return
    job.last_status_update = now
    try:
        await bot.edit_message_text(
            chat_id=job.chat_id,
            message_id=job.status_message_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=job_keyboard(job.job_id) if job.state in {"queued", "running"} else None,
        )
    except Exception as exc:
        # Telegram may reject an identical edit; this must never stop parsing.
        log.debug("Could not edit job status %s: %s", job.job_id, exc)


async def finish_job(bot: Bot, job: ScanJob, *, cancelled: bool = False) -> None:
    await finalize_user_scan(job, cancelled=cancelled)
    elapsed_seconds = max(0, int((datetime.utcnow() - job.created_at).total_seconds()))
    mins, secs = divmod(elapsed_seconds, 60)
    elapsed_text = f"{mins} мин {secs} сек" if mins else f"{secs} сек"

    job.state = "cancelled" if cancelled else "done"
    if cancelled:
        text = (
            "❌ <b>Парсинг отменён</b>\n\n"
            f"Категорий обработано: <b>{job.completed_categories}/{len(job.category_keys)}</b>\n"
            f"Новых найдено: <b>{job.total_new}</b>\n"
            f"⏱ Время: <b>{elapsed_text}</b>"
        )
    else:
        text = (
            "✅ <b>Парсинг завершён</b>\n\n"
            f"🗂 Категорий обработано: <b>{job.completed_categories}/{len(job.category_keys)}</b>\n"
            f"🆕 Новых объявлений: <b>{job.total_new}</b>\n"
            f"⏱ Время: <b>{elapsed_text}</b>\n\n"
            "📄 Формирую файл с результатом…"
        )
    await edit_job_status(bot, job, text, force=True)

    if not cancelled:
        try:
            settings = await get_settings(job.user_id)
            result_prefix = (
                "✅ <b>Готовый результат</b>\n"
                f"🗂 Категорий: <b>{job.completed_categories}/{len(job.category_keys)}</b>\n"
                f"📄 Глубина: <b>{job.page_limit} страниц на категорию</b>\n"
                f"🆕 Новых найдено: <b>{job.total_new}</b>\n"
                f"⏱ Время: <b>{elapsed_text}</b>\n"
                f"Режим: <b>{MODE_LABELS.get(settings.output_mode, settings.output_mode)}</b>\n"
                f"Период: <b>{PERIOD_LABELS.get(settings.period, settings.period)}</b>\n"
                f"Цена: <b>{PRICE_LABELS.get(settings.price_filter, settings.price_filter)}</b>"
            )
            adapter = BotChatAdapter(
                bot,
                job.chat_id,
                prefix=result_prefix,
                reply_markup=post_scan_keyboard(job.scan_id),
            )
            await send_smart_export(
                adapter,
                job.user_id,
                len(job.category_keys),
                category_keys_override=set(job.category_keys),
            )
        except Exception as exc:
            log.exception("Could not auto-export result for job=%s", job.job_id)
            try:
                await bot.send_message(
                    job.chat_id,
                    "⚠️ Парсинг завершён, но автоматическую выгрузку сформировать не удалось. "
                    "Нажми «📦 Получить результат» — данные уже сохранены.",
                    reply_markup=post_scan_keyboard(job.scan_id),
                )
            except Exception:
                pass

    if job.warnings:
        try:
            await bot.send_message(
                job.chat_id,
                "<b>⚠️ Предупреждения</b>\n\n" + "\n".join(f"• {html.escape(x)}" for x in job.warnings[:20]),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            log.exception("Could not send warnings for job=%s", job.job_id)


async def process_scan_job(bot: Bot, job: ScanJob, worker_id: int) -> None:
    job.state = "running"
    if job.scan_id is not None:
        async with SessionLocal() as session:
            scan = await session.get(UserScan, job.scan_id)
            if scan is not None:
                scan.status = "running"
                await session.commit()
    job.worker_id = worker_id
    job.started_running_monotonic = time.monotonic()
    job.warnings = job.warnings or []
    await edit_job_status(bot, job, render_user_job_status(job), force=True)

    for idx, key in enumerate(job.category_keys, start=1):
        if job.cancel_requested:
            break
        cat = CATEGORIES.get(key)
        if cat is None:
            job.warnings.append(f"Неизвестная категория: {key}")
            continue
        job.current_category = cat.name
        job.current_category_key = cat.key
        job.current_progress_key = f"{cat.key}:{job.page_limit}"
        job.current_category_index = idx
        try:
            await edit_job_status(bot, job, render_user_job_status(job), force=True)
            dispatched = await dispatch_category(cat, job.user_id, job.page_limit)
            result = dispatched.result
            source_label = "🧠 кэш"
            if dispatched.source == "cache":
                job.cache_hits += 1
                source_label = f"🧠 кэш ({dispatched.cache_age_seconds} сек.)"
            elif dispatched.source == "shared":
                job.shared_hits += 1
                source_label = "🤝 общий скан"
            else:
                job.scanned_categories += 1
                source_label = "🌐 новый скан"

            if result is not None:
                # New_count is a global DB fact from this shared scan. Pages are counted
                # only for the job that actually started the network scan.
                job.total_new += result.new_count
                if dispatched.source == "scan":
                    job.total_pages += result.pages_scanned
                    job.total_avoided += result.avoided_pages
                    if result.mode == "fast":
                        job.fast_categories += 1
                    else:
                        job.full_categories += 1
                if result.hit_limit:
                    job.warnings.append(f"{cat.name}: достигнут выбранный лимит {job.page_limit} страниц")
                elif result.reason.startswith("временный лимит Kleinanzeigen"):
                    job.warnings.append(
                        f"{cat.name}: Kleinanzeigen временно ограничил запросы; сохранено {result.pages_scanned} стр., можно повторить позже"
                    )

            job.completed_categories += 1
            # User sees only useful progress; cache/shared/worker details stay internal.
            await edit_job_status(bot, job, render_user_job_status(job), force=True)
        except Exception as exc:
            log.exception("Queue scan error job=%s category=%s", job.job_id, cat.name)
            job.warnings.append(f"{cat.name}: {str(exc)[:120]}")
            job.completed_categories += 1

    await finish_job(bot, job, cancelled=job.cancel_requested)


async def scan_worker(bot: Bot, worker_id: int) -> None:
    log.info("Scan worker #%s started", worker_id)
    while True:
        job = await scan_queue.get()
        try:
            async with job_guard:
                if job.job_id in queued_job_ids:
                    queued_job_ids.remove(job.job_id)
                if job.cancel_requested:
                    job.state = "cancelled"
                else:
                    job.state = "running"
                    job.worker_id = worker_id

            if job.cancel_requested:
                await finish_job(bot, job, cancelled=True)
            else:
                await process_scan_job(bot, job, worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Unhandled scan worker error worker=%s job=%s", worker_id, job.job_id)
            job.state = "failed"
            if job.scan_id is not None:
                try:
                    async with SessionLocal() as session:
                        scan = await session.get(UserScan, job.scan_id)
                        if scan is not None:
                            scan.status = "failed"
                            scan.finished_at = datetime.utcnow()
                            await session.commit()
                except Exception:
                    log.exception("Could not mark user scan failed scan_id=%s", job.scan_id)
            try:
                await edit_job_status(bot, job, "❌ <b>Ошибка задания парсинга</b>\nПопробуй запустить ещё раз.", force=True)
            except Exception:
                pass
        finally:
            async with job_guard:
                if active_jobs.get(job.user_id) is job:
                    active_jobs.pop(job.user_id, None)
                if job.job_id in queued_job_ids:
                    queued_job_ids.remove(job.job_id)
            scan_queue.task_done()



async def enqueue_user_scan(message: Message, user_id: int, category_keys: list[str], page_limit: int) -> ScanJob:
    """Create a persistent scan card and queue the network job."""
    job_uid = uuid.uuid4().hex[:12]
    scan = await create_user_scan(user_id, job_uid, category_keys, page_limit)
    status = await message.answer("⏳ <b>Подготавливаю скан…</b>", parse_mode=ParseMode.HTML)
    job = ScanJob(
        job_id=job_uid,
        user_id=user_id,
        chat_id=message.chat.id,
        status_message_id=status.message_id,
        category_keys=category_keys,
        created_at=datetime.utcnow(),
        warnings=[],
        page_limit=page_limit,
        scan_id=scan.id,
    )
    async with job_guard:
        active_jobs[job.user_id] = job
        queued_job_ids.append(job.job_id)
        scan_queue.put_nowait(job)
    await status.edit_text(
        render_user_job_status(job),
        parse_mode=ParseMode.HTML,
        reply_markup=job_keyboard(job.job_id),
    )
    return job



dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    selected = await get_selected(message.from_user.id)
    await message.answer(
        "<b>🔍 Kleinanzeigen Parser v2.8.0</b>\n\n"
        "Здесь всё строится вокруг сохранённых сканов:\n"
        "🔥 <b>Популярное сейчас</b> — лидеры по просмотрам\n"
        "🔎 <b>Новый скан</b> — собрать свежие объявления\n"
        "📊 <b>Мои сканы</b> — вернуться к любому запуску, обновить просмотры и увидеть рост\n\n"
        "После скана результат не теряется: его карточка остаётся в «Мои сканы».",
        reply_markup=main_keyboard(len(selected)), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    selected = await get_selected(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text("<b>🔍 Kleinanzeigen Parser v2.8.0</b>\n\nЧто хочешь посмотреть?", reply_markup=main_keyboard(len(selected)), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "post_settings")
async def post_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    s = await get_settings(callback.from_user.id)
    await callback.answer()
    await callback.message.answer(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "post_home")
async def post_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    selected = await get_selected(callback.from_user.id)
    await callback.answer()
    await callback.message.answer(
        "<b>🔍 Kleinanzeigen Parser v2.8.0</b>\n\nЧто хочешь посмотреть?",
        reply_markup=main_keyboard(len(selected)),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    s = await get_settings(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "mode_help")
async def mode_help(callback: CallbackQuery) -> None:
    await callback.answer()
    text = (
        "<b>ℹ️ Критерии Smart Analytics v2.6.1</b>\n\n"
        "💎 <b>Уникальные</b> — группа модели встречается ровно 1 раз в выбранном периоде. "
        "Цвет, состояние и продавцовский текст не дробят группу.\n\n"
        "🔥 <b>Часто публикуемые</b> — минимум 3 разных ID одной модели/варианта. "
        "В отчёте есть количество, минимум, медиана и максимум цены.\n\n"
        "💰 <b>Ниже рынка</b> — минимум 5 объявлений с ценой в одной группе; "
        "цена кандидата минимум на 20% ниже медианы остальных объявлений. 1 €-заглушки отсекаются.\n\n"
        "⚡ <b>Быстро исчезающие</b> — исчезли примерно за ≤12 часов. "
        "Точность зависит от интервала между последней успешной проверкой и обнаружением исчезновения.\n\n"
        "📉 <b>Снижение цены</b> — тот же ID стал дешевле минимум на 5 € и 5%.\n\n"
        "🚫 <b>Умные дубли</b> — схлопываются только очень похожие объявления с одинаковой ценой; "
        "аналитические группы при этом строятся отдельно."
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ К настройкам", callback_data="settings")]]
    ))


@dp.callback_query(F.data.startswith("quickmode:"))
async def quick_mode(callback: CallbackQuery) -> None:
    value = callback.data.split(":", 1)[1]
    if value not in MODE_LABELS:
        await callback.answer("Режим не найден", show_alert=True)
        return
    s = await update_setting(callback.from_user.id, "output_mode", value)
    await callback.answer(f"Выбрано: {MODE_LABELS[value]}")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "set_mode")
async def set_mode(callback: CallbackQuery) -> None:
    await callback.answer()
    opts = [(k, v) for k, v in MODE_LABELS.items()]
    await callback.message.edit_text("<b>Режим результата</b>\n\nВыбери, что попадёт в файл:", reply_markup=choice_keyboard("mode", opts), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("mode:"))
async def choose_mode(callback: CallbackQuery) -> None:
    value = callback.data.split(":", 1)[1]
    if value not in MODE_LABELS: return
    s = await update_setting(callback.from_user.id, "output_mode", value)
    await callback.answer("Режим сохранён")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "toggle_dedupe")
async def toggle_dedupe(callback: CallbackQuery) -> None:
    old = await get_settings(callback.from_user.id)
    s = await update_setting(callback.from_user.id, "smart_dedupe", not old.smart_dedupe)
    await callback.answer("Обновлено")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "toggle_noise")
async def toggle_noise(callback: CallbackQuery) -> None:
    old = await get_settings(callback.from_user.id)
    s = await update_setting(callback.from_user.id, "clean_noise", not old.clean_noise)
    await callback.answer("Обновлено")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "set_period")
async def set_period(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("<b>🕐 Период результата</b>", reply_markup=choice_keyboard("period", [(k, v) for k, v in PERIOD_LABELS.items()]), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("period:"))
async def choose_period(callback: CallbackQuery) -> None:
    value = callback.data.split(":", 1)[1]
    if value not in PERIOD_LABELS: return
    s = await update_setting(callback.from_user.id, "period", value)
    await callback.answer("Период сохранён")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "set_price")
async def set_price(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("<b>💶 Диапазон цены</b>", reply_markup=choice_keyboard("price", [(k, v) for k, v in PRICE_LABELS.items()]), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("price:"))
async def choose_price(callback: CallbackQuery) -> None:
    value = callback.data.split(":", 1)[1]
    if value not in PRICE_LABELS: return
    s = await update_setting(callback.from_user.id, "price_filter", value)
    await callback.answer("Цена сохранена")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "set_sort")
async def set_sort(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("<b>↕️ Сортировка</b>", reply_markup=choice_keyboard("sort", [(k, v) for k, v in SORT_LABELS.items()]), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("sort:"))
async def choose_sort(callback: CallbackQuery) -> None:
    value = callback.data.split(":", 1)[1]
    if value not in SORT_LABELS: return
    s = await update_setting(callback.from_user.id, "sort_mode", value)
    await callback.answer("Сортировка сохранена")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "set_include")
async def set_include(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(SettingsInput.include_words)
    await callback.message.answer("✅ Пришли слова через запятую. В результат попадут объявления, содержащие хотя бы одно из них.\n\nНапример: <code>apple tv, playstation, macbook</code>\n\nЧтобы очистить — отправь <code>-</code>.", parse_mode=ParseMode.HTML)


@dp.message(SettingsInput.include_words)
async def save_include(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value == "-": value = ""
    s = await update_setting(message.from_user.id, "include_words", value[:1000])
    await state.clear()
    await message.answer("✅ Ключевые слова сохранены.\n\n" + settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "set_exclude")
async def set_exclude(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(SettingsInput.exclude_words)
    await callback.message.answer("🚫 Пришли исключаемые слова через запятую.\n\nНапример: <code>defekt, hülle, case</code>\n\nЧтобы очистить — отправь <code>-</code>.", parse_mode=ParseMode.HTML)


@dp.message(SettingsInput.exclude_words)
async def save_exclude(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value == "-": value = ""
    s = await update_setting(message.from_user.id, "exclude_words", value[:1000])
    await state.clear()
    await message.answer("🚫 Исключения сохранены.\n\n" + settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "reset_settings")
async def reset_settings(callback: CallbackQuery) -> None:
    s = await reset_user_settings(callback.from_user.id)
    await callback.answer("Настройки сброшены")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "view_test")
async def view_test(callback: CallbackQuery, state: FSMContext) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(SettingsInput.view_test_url)
    await callback.answer()
    await callback.message.answer(
        "<b>⚡ Тест быстрого получения просмотров</b>\n\n"
        "Пришли одну публичную ссылку Kleinanzeigen. Бот сравнит два способа:\n"
        "1) прямой запрос счётчика без открытия карточки;\n"
        "2) обычное открытие страницы через Chromium как контроль.\n\n"
        "Так мы сразу увидим, сколько времени экономит быстрый режим.",
        parse_mode=ParseMode.HTML,
    )


@dp.message(SettingsInput.view_test_url)
async def run_view_test(message: Message, state: FSMContext) -> None:
    if not allowed(message.from_user.id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    url = (message.text or "").strip()
    if not (url.startswith("https://") and "kleinanzeigen.de/s-anzeige/" in url):
        await message.answer("⚠️ Пришли именно публичную ссылку на объявление Kleinanzeigen или нажми /start для выхода.")
        return

    await state.clear()
    status = await message.answer(
        "⏳ <b>Сравниваю быстрый запрос и Chromium…</b>\n\n"
        "Тест может добавить единичные просмотры к объявлению.",
        parse_mode=ParseMode.HTML,
    )
    parser = KleinanzeigenParser()
    try:
        t0 = time.perf_counter()
        mode, direct = await parser.probe_direct_view_mode(url, force=True)
        direct_time = time.perf_counter() - t0

        t1 = time.perf_counter()
        browser = await parser.fetch_public_view_count(url, http_fast_path=False)
        browser_time = time.perf_counter() - t1
    finally:
        await parser.close()

    selected = await get_selected(message.from_user.id)
    lines = ["⚡ <b>Тест быстрого счётчика</b>", ""]

    if direct.views is not None:
        lines += [
            f"🚀 Прямой способ: <b>{direct.views}</b> просмотров",
            f"Источник: <code>{html.escape(direct.source)}</code>",
            f"Время: <b>{direct_time:.2f} сек</b>",
        ]
    else:
        lines += [
            "🚀 Прямой способ: <b>не сработал</b>",
            f"Режим: <code>{html.escape(mode)}</code>",
            f"Время попытки: <b>{direct_time:.2f} сек</b>",
        ]
        if direct.error:
            lines.append(f"Ошибка: <code>{html.escape(direct.error[:180])}</code>")

    lines.append("")
    if browser.views is not None:
        lines += [
            f"🌐 Chromium: <b>{browser.views}</b> просмотров",
            f"Источник: <code>{html.escape(browser.source)}</code>",
            f"Время: <b>{browser_time:.2f} сек</b>",
        ]
    else:
        lines += [
            "🌐 Chromium: <b>не удалось получить</b>",
            f"Время: <b>{browser_time:.2f} сек</b>",
        ]

    if direct.views is not None and browser_time > 0:
        speedup = browser_time / max(direct_time, 0.01)
        lines += [
            "",
            f"📈 Ускорение на тесте: <b>примерно ×{speedup:.1f}</b>",
            "✅ Обычный массовый парсинг v2.7.2 уже сначала использует быстрый способ. Chromium включается только для объявлений, где прямой счётчик не сработал.",
        ]
    else:
        lines += [
            "",
            "ℹ️ Для массового парсинга останется автоматический browser fallback, поэтому объявления не потеряются.",
        ]

    await status.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(len(selected)),
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data == "popular_now")
async def popular_now(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    await callback.answer()
    selected = await get_selected(callback.from_user.id)
    rows = await today_rows()
    if selected:
        rows = [r for r in rows if r.category_key in selected]
    rows = [r for r in rows if r.view_count is not None]
    rows.sort(key=lambda r: (r.view_count or 0, r.first_seen_at), reverse=True)
    top = rows[:12]
    if not top:
        text = "🔥 <b>Популярное сейчас</b>\n\nПока нет объявлений с просмотрами. Сначала запусти скан."
    else:
        lines = ["🔥 <b>Популярное сейчас</b>", "", "Лидеры по просмотрам среди свежих собранных объявлений:", ""]
        for i, row in enumerate(top, 1):
            title = html.escape(row.title[:55])
            price = html.escape(_price_display(row.price_text, row.price_eur))
            lines.append(f"<b>{i}. {title}</b>\n👁 {row.view_count} · 💶 {price}\n<a href=\"{html.escape(row.url)}\">Открыть объявление</a>")
        lines.append("\n🚀 Скорость роста появится в карточках «Мои сканы» после повторного обновления просмотров.")
        text = "\n\n".join(lines)
    await callback.message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=main_keyboard(len(selected)))


@dp.callback_query(F.data == "my_scans")
async def my_scans(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    scans = await get_user_scans(callback.from_user.id, 10)
    await callback.answer()
    if not scans:
        await callback.message.edit_text(
            "<b>📊 Мои сканы</b>\n\nПока пусто. Сделай первый скан — он сохранится здесь, и к нему можно будет вернуться позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=my_scans_keyboard([]),
        )
        return
    await callback.message.edit_text(
        "<b>📊 Мои сканы</b>\n\nОткрой нужный запуск. Внутри можно обновить просмотры, посмотреть рост, топ и повторить скан.",
        parse_mode=ParseMode.HTML,
        reply_markup=my_scans_keyboard(scans),
    )


async def render_scan_detail(scan: UserScan) -> str:
    pairs = await get_scan_rows(scan.id)
    rows = [listing for listing, _ in pairs]
    viewed = sum(1 for row in rows if row.view_count is not None)
    disappeared = sum(1 for row in rows if not row.is_active)
    growers = 0
    total_growth = 0
    for listing, snap in pairs:
        if listing.view_count is not None and snap.initial_view_count is not None:
            delta = listing.view_count - snap.initial_view_count
            if delta > 0:
                growers += 1
                total_growth += delta
    new_since = 0
    if scan.finished_at is not None:
        keys = _scan_category_keys(scan)
        if keys:
            async with SessionLocal() as session:
                new_since = (await session.execute(select(func.count(Listing.id)).where(
                    Listing.category_key.in_(keys), Listing.first_seen_at > scan.finished_at
                ))).scalar_one()
    status_label = {"done": "✅ завершён", "running": "🔄 идёт", "queued": "⏳ ожидает", "cancelled": "❌ отменён", "failed": "❌ ошибка"}.get(scan.status, scan.status)
    lines = [
        f"<b>📊 {html.escape(scan.title)}</b>",
        "",
        f"Статус: <b>{status_label}</b>",
        f"Запуск: <b>{_berlin_text(scan.created_at)}</b>",
        f"Глубина: <b>{scan.page_limit} стр. на категорию</b>",
        f"Категорий: <b>{scan.completed_categories}/{scan.total_categories}</b>",
        "",
        f"📦 В снимке: <b>{len(rows)}</b> объявлений",
        f"👁 С просмотрами: <b>{viewed}</b>",
        f"🚀 Выросли после скана: <b>{growers}</b>" + (f" · суммарно +{total_growth}" if total_growth else ""),
        f"🆕 Новых после скана, уже найденных последующими сканами: <b>{new_since}</b>",
        f"❌ Исчезли: <b>{disappeared}</b>",
    ]
    if scan.last_view_refresh_at:
        lines += ["", f"Последнее обновление просмотров: <b>{_berlin_text(scan.last_view_refresh_at)}</b>"]
    lines += ["", "💡 Нажми «Обновить просмотры» через час-два — бот сравнит новые значения с моментом завершения этого скана."]
    return "\n".join(lines)


@dp.callback_query(F.data.startswith("scan:"))
async def scan_detail(callback: CallbackQuery) -> None:
    try:
        scan_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Скан не найден", show_alert=True); return
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    await callback.answer()
    text = await render_scan_detail(scan)
    # A scan can be opened both from a normal text message and from the result-file caption.
    # Telegram cannot edit a document into text, so open a fresh card for document callbacks.
    if callback.message.text:
        await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=scan_detail_keyboard(scan.id), disable_web_page_preview=True
        )
    else:
        await callback.message.answer(
            text, parse_mode=ParseMode.HTML, reply_markup=scan_detail_keyboard(scan.id), disable_web_page_preview=True
        )


@dp.callback_query(F.data.startswith("scantop:"))
async def scan_top(callback: CallbackQuery) -> None:
    scan_id = int(callback.data.split(":", 1)[1])
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    pairs = await get_scan_rows(scan_id)
    pairs = [p for p in pairs if p[0].view_count is not None]
    pairs.sort(key=lambda p: p[0].view_count or 0, reverse=True)
    await callback.answer()
    if not pairs:
        text = "🔥 <b>Самые просматриваемые</b>\n\nПока нет данных просмотров."
    else:
        lines = [f"🔥 <b>Топ скана: {html.escape(scan.title)}</b>", ""]
        for i, (row, snap) in enumerate(pairs[:12], 1):
            delta = (row.view_count - snap.initial_view_count) if snap.initial_view_count is not None else None
            growth = f" · 🚀 +{delta}" if delta is not None and delta > 0 else ""
            lines.append(
                f"<b>{i}. {html.escape(row.title[:55])}</b>\n"
                f"👁 {row.view_count}{growth} · 💶 {html.escape(_price_display(row.price_text, row.price_eur))}\n"
                f"<a href=\"{html.escape(row.url)}\">Открыть</a>"
            )
        text = "\n\n".join(lines)
    await callback.message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=scan_detail_keyboard(scan_id))


@dp.callback_query(F.data.startswith("scangrowth:"))
async def scan_growth(callback: CallbackQuery) -> None:
    scan_id = int(callback.data.split(":", 1)[1])
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    pairs = await get_scan_rows(scan_id)
    growth = []
    elapsed_hours = max(1/60, ((datetime.utcnow() - (scan.finished_at or scan.created_at)).total_seconds() / 3600))
    for row, snap in pairs:
        if row.view_count is None or snap.initial_view_count is None:
            continue
        delta = row.view_count - snap.initial_view_count
        if delta > 0:
            growth.append((delta / elapsed_hours, delta, row))
    growth.sort(key=lambda x: (x[0], x[1]), reverse=True)
    await callback.answer()
    if not growth:
        text = (
            "🚀 <b>Рост просмотров</b>\n\nПока роста не зафиксировано. "
            "Обнови просмотры через некоторое время — тогда здесь появится динамика."
        )
    else:
        lines = [f"🚀 <b>Быстрее всего растут: {html.escape(scan.title)}</b>", ""]
        for i, (per_hour, delta, row) in enumerate(growth[:12], 1):
            lines.append(
                f"<b>{i}. {html.escape(row.title[:55])}</b>\n"
                f"🚀 +{delta} · ≈ {per_hour:.1f} просмотров/ч · 👁 {row.view_count}\n"
                f"💶 {html.escape(_price_display(row.price_text, row.price_eur))} · "
                f"<a href=\"{html.escape(row.url)}\">Открыть</a>"
            )
        text = "\n\n".join(lines)
    await callback.message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=scan_detail_keyboard(scan_id))


@dp.callback_query(F.data.startswith("scanviews:"))
async def scan_refresh_views(callback: CallbackQuery) -> None:
    scan_id = int(callback.data.split(":", 1)[1])
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    pairs = await get_scan_rows(scan_id)
    rows = [row for row, _ in pairs]
    if not rows:
        await callback.answer("В этом скане пока нет объявлений", show_alert=True); return
    await callback.answer("Обновляю просмотры")
    await refresh_view_counts(rows, callback.message)
    await update_scan_view_refresh(scan_id)
    scan = await get_user_scan(callback.from_user.id, scan_id)
    await callback.message.answer(
        await render_scan_detail(scan), parse_mode=ParseMode.HTML, reply_markup=scan_detail_keyboard(scan_id)
    )


@dp.callback_query(F.data.startswith("scanexport:"))
async def scan_export(callback: CallbackQuery) -> None:
    scan_id = int(callback.data.split(":", 1)[1])
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    pairs = await get_scan_rows(scan_id)
    rows = [row for row, _ in pairs]
    await callback.answer()
    await send_smart_export(
        callback.message, callback.from_user.id, scan.total_categories,
        category_keys_override=set(_scan_category_keys(scan)), rows_override=rows,
    )


@dp.callback_query(F.data.startswith("scanrepeat:"))
async def scan_repeat(callback: CallbackQuery) -> None:
    scan_id = int(callback.data.split(":", 1)[1])
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    async with job_guard:
        existing = active_jobs.get(callback.from_user.id)
        if existing and existing.state in {"queued", "running"} and not existing.cancel_requested:
            await callback.answer("У тебя уже идёт скан", show_alert=True); return
        if len(queued_job_ids) >= MAX_QUEUE_SIZE:
            await callback.answer("Сервис сейчас сильно загружен. Попробуй чуть позже.", show_alert=True); return
    keys = [k for k in _scan_category_keys(scan) if k in CATEGORIES]
    if not keys:
        await callback.answer("Категории этого скана больше недоступны", show_alert=True); return
    await callback.answer("Повторяю скан")
    await enqueue_user_scan(callback.message, callback.from_user.id, keys, scan.page_limit)


@dp.callback_query(F.data == "groups")
async def groups(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id): await callback.answer("Нет доступа", show_alert=True); return
    selected = await get_selected(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text("<b>🗂 Категории Kleinanzeigen</b>\n\nОткрой раздел и отметь, что нужно парсить.", reply_markup=groups_keyboard(selected), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("grp:"))
async def open_group(callback: CallbackQuery) -> None:
    group_key = callback.data.split(":", 1)[1]
    if group_key not in GROUPS: await callback.answer("Раздел не найден", show_alert=True); return
    selected = await get_selected(callback.from_user.id)
    group = GROUPS[group_key]
    await callback.answer()
    await callback.message.edit_text(f"<b>{group.icon} {html.escape(group.name)}</b>\n\nВыбери весь раздел или отдельные подкатегории.", reply_markup=category_keyboard(group_key, selected), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("cat:"))
async def toggle_cat(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    if key not in CATEGORIES: await callback.answer("Категория не найдена", show_alert=True); return
    selected = await toggle_category(callback.from_user.id, key)
    await callback.answer("Выбор обновлён")
    await callback.message.edit_reply_markup(reply_markup=category_keyboard(CATEGORIES[key].group, selected))


@dp.callback_query(F.data.startswith("grpall:"))
async def toggle_all_children(callback: CallbackQuery) -> None:
    group_key = callback.data.split(":", 1)[1]
    if group_key not in GROUPS: return
    selected = await toggle_group_children(callback.from_user.id, group_key)
    await callback.answer("Выбор обновлён")
    await callback.message.edit_reply_markup(reply_markup=category_keyboard(group_key, selected))


@dp.callback_query(F.data == "clear_all")
async def clear_all(callback: CallbackQuery) -> None:
    await clear_selected(callback.from_user.id)
    await callback.answer("Выбор очищен")
    await callback.message.edit_reply_markup(reply_markup=groups_keyboard(set()))


@dp.callback_query(F.data == "selected")
async def selected(callback: CallbackQuery) -> None:
    keys = await get_selected(callback.from_user.id)
    cats = [CATEGORIES[k] for k in CATEGORIES if k in keys]
    if not cats:
        text = "<b>Категории пока не выбраны.</b>"
    else:
        lines = [f"<b>Выбрано категорий: {len(cats)}</b>", ""]
        for cat in cats[:70]: lines.append(f"• {html.escape(cat.name)}")
        if len(cats) > 70: lines.append(f"…и ещё {len(cats)-70}")
        text = "\n".join(lines)
    await callback.answer()
    await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(len(keys)))


@dp.callback_query(F.data == "stats")
async def stats(callback: CallbackQuery) -> None:
    await callback.answer()
    keys = await get_selected(callback.from_user.id)
    await callback.message.answer(await stats_text(), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(len(keys)))


@dp.callback_query(F.data == "export_smart")
async def export_smart(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id): await callback.answer("Нет доступа", show_alert=True); return
    await callback.answer()
    selected = await get_selected(callback.from_user.id)
    await send_smart_export(callback.message, callback.from_user.id, len(selected))


@dp.callback_query(F.data == "queue_status")
async def queue_status(callback: CallbackQuery) -> None:
    # Backward compatibility for old bot messages: never expose global queue/workers.
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    async with job_guard:
        job = active_jobs.get(callback.from_user.id)
    if job and job.state in {"queued", "running"}:
        text = render_user_job_status(job)
        markup = job_keyboard(job.job_id)
    else:
        text = "✅ Сейчас у тебя нет активного парсинга."
        selected = await get_selected(callback.from_user.id)
        markup = main_keyboard(len(selected))
    await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)


@dp.callback_query(F.data.startswith("cancel_scan:"))
async def cancel_scan(callback: CallbackQuery) -> None:
    job_id = callback.data.split(":", 1)[1]
    async with job_guard:
        job = active_jobs.get(callback.from_user.id)
        if job is None or job.job_id != job_id:
            await callback.answer("Активная задача уже не найдена", show_alert=True)
            return
        job.cancel_requested = True
        if job.job_id in queued_job_ids:
            queued_job_ids.remove(job.job_id)
        state = job.state
    if state == "queued":
        await callback.answer("Задача отменена")
        await callback.message.edit_text(
            "❌ <b>Парсинг отменён</b>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await callback.answer("Отмена принята")
        await callback.message.edit_text(
            "⏳ <b>Останавливаю парсинг…</b>\n\nТекущая категория завершится, после чего запуск остановится.",
            parse_mode=ParseMode.HTML,
        )


@dp.callback_query(F.data == "start_scan")
async def start_scan(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    selected_keys = await get_selected(callback.from_user.id)
    selected_cats = [CATEGORIES[k] for k in CATEGORIES if k in selected_keys]
    if not selected_cats:
        await callback.answer("Сначала выбери хотя бы одну категорию", show_alert=True)
        return

    async with job_guard:
        existing = active_jobs.get(callback.from_user.id)
        if existing and existing.state in {"queued", "running"} and not existing.cancel_requested:
            await callback.answer("У тебя уже идёт парсинг", show_alert=True)
            return

    await callback.answer()
    await callback.message.answer(
        "<b>🔎 Глубина нового скана</b>\n\n"
        f"Выбрано категорий: <b>{len(selected_cats)}</b>\n"
        "Лимит применяется <b>к каждой выбранной категории</b>.\n\n"
        "25 — быстрый сбор\n"
        "50 — средняя глубина\n"
        "100 — максимальная глубина",
        parse_mode=ParseMode.HTML,
        reply_markup=page_limit_keyboard(),
    )


@dp.callback_query(F.data.startswith("scanpages:"))
async def start_scan_with_pages(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    try:
        page_limit = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный лимит", show_alert=True)
        return
    if page_limit not in PAGE_LIMIT_CHOICES:
        await callback.answer("Выбери 25, 50 или 100 страниц", show_alert=True)
        return

    selected_keys = await get_selected(callback.from_user.id)
    selected_cats = [CATEGORIES[k] for k in CATEGORIES if k in selected_keys]
    if not selected_cats:
        await callback.answer("Сначала выбери хотя бы одну категорию", show_alert=True)
        return

    async with job_guard:
        existing = active_jobs.get(callback.from_user.id)
        if existing and existing.state in {"queued", "running"} and not existing.cancel_requested:
            await callback.answer("У тебя уже идёт парсинг", show_alert=True)
            return
        if len(queued_job_ids) >= MAX_QUEUE_SIZE:
            await callback.answer("Сервис сейчас сильно загружен. Попробуй чуть позже.", show_alert=True)
            return

    await update_setting(callback.from_user.id, "page_limit", page_limit)
    await callback.answer("Запускаю скан")
    await enqueue_user_scan(
        callback.message, callback.from_user.id, [cat.key for cat in selected_cats], page_limit
    )


async def main() -> None:
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not set")
    await init_db()
    bot = Bot(BOT_TOKEN)
    me = await bot.get_me()
    log.info(
        "Starting @%s | workers=%s cache_ttl=%ss",
        me.username, MAX_CONCURRENT_JOBS, CATEGORY_CACHE_TTL_SECONDS,
    )
    if not USING_PERSISTENT_DATABASE:
        log.warning(
            "DATABASE_URL is not set. v2.6 queue/cache work on SQLite, but the queue is in-memory "
            "and SQLite data may be lost after Railway redeploy/restart. PostgreSQL is the next step."
        )

    worker_tasks = [
        asyncio.create_task(scan_worker(bot, i), name=f"scan-worker-{i}")
        for i in range(1, MAX_CONCURRENT_JOBS + 1)
    ]
    ticker_task = asyncio.create_task(progress_ticker(bot), name="user-progress-ticker")
    try:
        await dp.start_polling(bot)
    finally:
        ticker_task.cancel()
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(ticker_task, *worker_tasks, return_exceptions=True)
        async with category_inflight_guard:
            inflight = list(category_inflight.values())
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
