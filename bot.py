from __future__ import annotations

import asyncio
import csv
from collections import Counter
import html
import logging
import os
import re
import shutil
import statistics
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
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

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
from models import CategoryScanState, Listing, ParserRun, PriceHistory, ScanListing, ScanObservation, ScanViewHistory, SelectedCategory, UserScan, UserSettings, ViewHistory
from product_identity import TYPE_DISPLAY, ProductIdentity, recognize_product
from traffic import TRAFFIC
from parser import (
    MAX_PAGES_PER_CATEGORY,
    PAGE_DELAY_SECONDS,
    STOP_AFTER_EMPTY_TODAY_PAGES,
    KleinanzeigenParser,
    ParsedListing,
    ViewCountResult,
    TemporaryAccessError,
    is_today_text,
    posted_date_moscow,
    profile_page_dates,
    page_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("kleinanzeigen-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
BERLIN = ZoneInfo("Europe/Berlin")
MOSCOW = ZoneInfo("Europe/Moscow")
AVAILABILITY_CHECK_LIMIT = max(1, int(os.getenv("AVAILABILITY_CHECK_LIMIT", "150")))
AVAILABILITY_CONCURRENCY = max(1, min(8, int(os.getenv("AVAILABILITY_CONCURRENCY", "4"))))

# v2.6 Multi-User Core. User launches go into a queue. Only a limited number
# of jobs are processed at once, while category scans are shared globally.
MAX_CONCURRENT_JOBS = max(1, min(12, int(os.getenv("MAX_CONCURRENT_JOBS", "4"))))
MAX_QUEUE_SIZE = max(10, int(os.getenv("MAX_QUEUE_SIZE", "200")))
CATEGORY_CACHE_TTL_SECONDS = max(0, int(os.getenv("CATEGORY_CACHE_TTL_SECONDS", "300")))
STATUS_UPDATE_INTERVAL_SECONDS = max(0.5, float(os.getenv("STATUS_UPDATE_INTERVAL_SECONDS", "1.5")))

# Public view counts are collected inline while category pages are scanned.
# Recent values are cached so shared/multi-user scans do not reopen the same ad.
VIEW_COUNT_CACHE_TTL_SECONDS = max(60, int(os.getenv("VIEW_COUNT_CACHE_TTL_SECONDS", "1800")))
VIEW_COUNT_CONCURRENCY = max(1, min(10, int(os.getenv("VIEW_COUNT_CONCURRENCY", "5"))))
VIEW_COUNT_EXPORT_MODES = {"newest", "all", "unique", "below_market"}

# v3.1 keeps the v3.0.7 Popularity Tracker. Every completed scan gets automatic public-view
# checkpoints. They are persisted, so a Railway restart does not lose the plan.
OBSERVATION_HOURS = (1, 3, 6, 12, 24)
OBSERVATION_POLL_SECONDS = max(15, int(os.getenv("OBSERVATION_POLL_SECONDS", "30")))
OBSERVATION_CONCURRENCY = max(1, min(4, int(os.getenv("OBSERVATION_CONCURRENCY", "2"))))
OBSERVATION_LATE_GRACE_MINUTES = max(5, int(os.getenv("OBSERVATION_LATE_GRACE_MINUTES", "45")))
GROWTH_TOP_LIMIT = 50
GROWTH_TELEGRAM_LIMIT = 10

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

# v3.0.6 exact-date mode. Kleinanzeigen's public search feed only exposes a
# limited pagination window. Requests above that window may normalize to/repeat
# another page, so a single nationwide feed cannot be used to jump arbitrarily
# deep. We keep the user's simple 25/50/100 depth, but when the chosen date is
# beyond the public window we transparently use disjoint federal-state feeds and
# merge unique target-day listings until the requested depth-equivalent is filled.
PUBLIC_SEARCH_PAGE_CAP = max(10, min(50, int(os.getenv("PUBLIC_SEARCH_PAGE_CAP", "50"))))
DATE_JUMP_PROBE_DELAY_SECONDS = max(0.0, min(1.0, float(os.getenv("DATE_JUMP_PROBE_DELAY_SECONDS", "0.18"))))

# Hidden implementation detail: these location shards cover Germany without
# intentionally overlapping. They are not shown to end users; the UI remains
# category + date + 25/50/100 pages. Smaller feeds are tried first because older
# dates are more likely to remain inside the public 50-page window.
GERMAN_STATE_SEGMENTS = (
    ("Bremen", "bremen", 1),
    ("Saarland", "saarland", 285),
    ("Hamburg", "hamburg", 9409),
    ("Mecklenburg-Vorpommern", "mecklenburg-vorpommern", 61),
    ("Thüringen", "thueringen", 3548),
    ("Sachsen-Anhalt", "sachsen-anhalt", 2165),
    ("Brandenburg", "brandenburg", 7711),
    ("Schleswig-Holstein", "schleswig-holstein", 408),
    ("Berlin", "berlin", 3331),
    ("Rheinland-Pfalz", "rheinland-pfalz", 4938),
    ("Sachsen", "sachsen", 3799),
    ("Hessen", "hessen", 4279),
    ("Niedersachsen", "niedersachsen", 2428),
    ("Baden-Württemberg", "baden-wuerttemberg", 7970),
    ("Bayern", "bayern", 5510),
    ("Nordrhein-Westfalen", "nordrhein-westfalen", 928),
)

def _regional_category_url(base_url: str, slug: str, location_id: int) -> str:
    m = re.match(r"^(https://www\.kleinanzeigen\.de/.+)/(c\d+)$", base_url.rstrip("/"))
    if not m:
        raise ValueError(f"Unsupported Kleinanzeigen category URL: {base_url}")
    return f"{m.group(1)}/{slug}/{m.group(2)}l{int(location_id)}"


class SettingsInput(StatesGroup):
    include_words = State()
    exclude_words = State()
    view_test_url = State()


class ScanInput(StatesGroup):
    target_date = State()


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
            InlineKeyboardButton(text="🚀 Динамика", callback_data=f"scangrowth:{scan_id}:1"),
        ])
        rows.append([InlineKeyboardButton(text="🧠 Распознанные модели", callback_data=f"scanproducts:{scan_id}")])
    else:
        rows.append([InlineKeyboardButton(text="🔄 Запустить снова", callback_data="start_scan")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="post_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def scan_detail_keyboard(scan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Обновить просмотры", callback_data=f"scanviews:{scan_id}"),
         InlineKeyboardButton(text="🔄 Пересканировать", callback_data=f"scanrepeat:{scan_id}")],
        [InlineKeyboardButton(text="🔥 Самые просматриваемые", callback_data=f"scantop:{scan_id}"),
         InlineKeyboardButton(text="🧠 Модели", callback_data=f"scanproducts:{scan_id}")],
        [InlineKeyboardButton(text="🚀 TOP роста", callback_data=f"scangrowth:{scan_id}:1"),
         InlineKeyboardButton(text="🕘 История", callback_data=f"scanhistory:{scan_id}")],
        [InlineKeyboardButton(text="📄 Файл этого скана", callback_data=f"scanexport:{scan_id}")],
        [InlineKeyboardButton(text="⬅️ Мои сканы", callback_data="my_scans"),
         InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
    ])


def growth_period_keyboard(scan_id: int, active_hours: int = 1, category_key: str | None = None) -> InlineKeyboardMarkup:
    prefix = f"pcg:{scan_id}:{category_key}:" if category_key else f"scangrowth:{scan_id}:"
    export_prefix = f"pce:{scan_id}:{category_key}:" if category_key else f"scangrowthexport:{scan_id}:"
    def b(hours: int) -> InlineKeyboardButton:
        label = f"{hours}ч"
        if hours == active_hours:
            label = "✅ " + label
        return InlineKeyboardButton(text=label, callback_data=f"{prefix}{hours}")

    back_callback = f"popularcat:{category_key}" if category_key else f"scan:{scan_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [b(1), b(3), b(6)],
        [b(12), b(24)],
        [InlineKeyboardButton(text="📊 Скачать TOP-50", callback_data=f"{export_prefix}{active_hours}")],
        [InlineKeyboardButton(text="👁 Обновить сейчас", callback_data=f"scanviews:{scan_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)],
    ])


def popular_categories_keyboard(items: list[tuple[str, UserScan]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, scan in items[:30]:
        cat = CATEGORIES.get(key)
        if cat is None:
            continue
        icon = GROUPS.get(cat.group).icon if cat.group in GROUPS else "📂"
        rows.append([InlineKeyboardButton(text=f"{icon} {cat.name[:38]}", callback_data=f"popularcat:{key}")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def popular_category_keyboard(scan_id: int, category_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Самые просматриваемые", callback_data=f"pcv:{scan_id}:{category_key}")],
        [InlineKeyboardButton(text="🚀 TOP 1ч", callback_data=f"pcg:{scan_id}:{category_key}:1"),
         InlineKeyboardButton(text="🚀 TOP 3ч", callback_data=f"pcg:{scan_id}:{category_key}:3")],
        [InlineKeyboardButton(text="🔥 TOP 6ч", callback_data=f"pcg:{scan_id}:{category_key}:6"),
         InlineKeyboardButton(text="🔥 TOP 12ч", callback_data=f"pcg:{scan_id}:{category_key}:12")],
        [InlineKeyboardButton(text="📈 TOP 24ч", callback_data=f"pcg:{scan_id}:{category_key}:24")],
        [InlineKeyboardButton(text="⬅️ Категории", callback_data="popular_now"),
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


@dataclass
class GrowthMetric:
    listing: Listing
    base_views: int
    current_views: int
    delta: int
    elapsed_hours: float
    per_hour: float
    observed_at: datetime


async def create_user_scan(user_id: int, job_uid: str, category_keys: list[str], page_limit: int, target_date: str) -> UserScan:
    scan = UserScan(
        job_uid=job_uid,
        user_id=user_id,
        title=_scan_title(category_keys),
        category_keys=",".join(category_keys),
        page_limit=page_limit,
        target_date=target_date,
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


async def get_user_popular_categories(user_id: int, limit_scans: int = 100) -> list[tuple[str, UserScan]]:
    """Return scanned categories, newest scan first, one latest scan per category."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(UserScan)
            .where(
                UserScan.user_id == user_id,
                UserScan.status.in_(["done", "partial"]),
            )
            .order_by(UserScan.finished_at.desc(), UserScan.created_at.desc())
            .limit(limit_scans)
        )
        scans = list(result.scalars().all())
    latest: dict[str, UserScan] = {}
    for scan in scans:
        for key in _scan_category_keys(scan):
            if key in CATEGORIES and key not in latest:
                latest[key] = scan
    return list(latest.items())


async def get_latest_scan_for_category(user_id: int, category_key: str) -> UserScan | None:
    items = await get_user_popular_categories(user_id)
    for key, scan in items:
        if key == category_key:
            return scan
    return None


async def get_scan_rows(scan_id: int) -> list[tuple[Listing, ScanListing]]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing, ScanListing)
            .join(ScanListing, Listing.external_id == ScanListing.external_id)
            .where(ScanListing.scan_id == scan_id)
        )
        return list(result.all())


async def ensure_scan_observation_plan(scan_id: int, finished_at: datetime | None = None) -> None:
    """Create the +1/+3/+6/+12/+24h plan once; safe to call after restarts."""
    async with db_write_lock:
        async with SessionLocal() as session:
            scan = await session.get(UserScan, scan_id)
            if scan is None or scan.status not in {"done", "partial"}:
                return
            base = finished_at or scan.finished_at or scan.created_at
            existing = await session.execute(
                select(ScanObservation.target_hours).where(ScanObservation.scan_id == scan_id)
            )
            have = {int(x) for x in existing.scalars().all()}
            for hours in OBSERVATION_HOURS:
                if hours in have:
                    continue
                session.add(ScanObservation(
                    scan_id=scan_id,
                    target_hours=hours,
                    due_at=base + timedelta(hours=hours),
                    status="pending",
                ))
            await session.commit()


async def get_scan_observation_statuses(scan_id: int) -> dict[int, str]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ScanObservation.target_hours, ScanObservation.status)
            .where(ScanObservation.scan_id == scan_id)
            .order_by(ScanObservation.target_hours)
        )
        return {int(hours): status for hours, status in result.all()}


async def backfill_recent_observation_plans() -> int:
    """Attach the new plan to still-relevant scans from the last 24h."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    async with SessionLocal() as session:
        result = await session.execute(
            select(UserScan.id, UserScan.finished_at)
            .where(
                UserScan.status.in_(["done", "partial"]),
                UserScan.finished_at.is_not(None),
                UserScan.finished_at >= cutoff,
            )
        )
        rows = list(result.all())
    for scan_id, finished_at in rows:
        await ensure_scan_observation_plan(int(scan_id), finished_at)
    return len(rows)


async def finalize_user_scan(job: "ScanJob", *, cancelled: bool = False) -> None:
    if job.scan_id is None:
        return
    now = datetime.utcnow()
    async with db_write_lock:
        async with SessionLocal() as session:
            scan = await session.get(UserScan, job.scan_id)
            if scan is None:
                return
            scan.status = "cancelled" if cancelled else ("partial" if job.incomplete_categories else "done")
            scan.finished_at = now
            scan.completed_categories = job.completed_categories
            scan.total_categories = len(job.category_keys)
            scan.new_count = job.total_new
            scan.target_complete = bool(not cancelled and job.incomplete_categories == 0)
            scan.scan_note = " | ".join((job.scan_notes or [])[:4])[:500]
            quality_scores = [int(x) for x in (job.quality_scores or []) if x is not None]
            scan.quality_score = round(sum(quality_scores) / len(quality_scores)) if quality_scores else 0
            scan.quality_note = " | ".join((job.quality_notes or [])[:4])[:500]
            if cancelled:
                await session.commit()
                return

            matched_ids = sorted(job.matched_ids or set())
            if matched_ids:
                result = await session.execute(select(Listing).where(
                    Listing.external_id.in_(matched_ids),
                    Listing.category_key.in_(job.category_keys),
                    Listing.posted_date_msk == job.target_date,
                ))
                rows = list(result.scalars().all())
            else:
                rows = []
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
                if row.view_count is not None:
                    session.add(ScanViewHistory(
                        scan_id=scan.id,
                        external_id=row.external_id,
                        view_count=int(row.view_count),
                        recorded_at=now,
                    ))
            await session.commit()

    if not cancelled:
        await ensure_scan_observation_plan(job.scan_id, now)


async def update_scan_view_refresh(scan_id: int, target_hours: int | None = None) -> int:
    """Store one complete view observation round for a saved scan."""
    now = datetime.utcnow()
    async with db_write_lock:
        async with SessionLocal() as session:
            scan = await session.get(UserScan, scan_id)
            if scan is None:
                return 0
            query = (
                select(Listing.external_id, Listing.view_count)
                .join(ScanListing, Listing.external_id == ScanListing.external_id)
                .where(ScanListing.scan_id == scan_id)
            )
            if target_hours is not None:
                # Scheduled checkpoints must contain freshly read counters, not stale values
                # left in the listing table after a failed request.
                query = query.where(
                    Listing.views_checked_at.is_not(None),
                    Listing.views_checked_at >= now - timedelta(minutes=15),
                )
            result = await session.execute(query)
            values = list(result.all())
            recorded = 0
            for external_id, view_count in values:
                if view_count is None:
                    continue
                session.add(ScanViewHistory(
                    scan_id=scan_id,
                    external_id=external_id,
                    view_count=int(view_count),
                    recorded_at=now,
                    target_hours=target_hours,
                ))
                recorded += 1
            scan.viewed_count = recorded
            scan.last_view_refresh_at = now
            await session.commit()
            return recorded


async def get_scan_history_rounds(scan_id: int, limit: int = 12) -> list[tuple[datetime, int, int]]:
    """Return observation rounds, including v2.8 scan snapshots as a baseline."""
    async with SessionLocal() as session:
        live_result = await session.execute(
            select(
                ScanViewHistory.recorded_at,
                func.count(ScanViewHistory.id),
                func.sum(ScanViewHistory.view_count),
            )
            .where(ScanViewHistory.scan_id == scan_id)
            .group_by(ScanViewHistory.recorded_at)
        )
        baseline_result = await session.execute(
            select(
                ScanListing.captured_at,
                func.count(ScanListing.id),
                func.sum(ScanListing.initial_view_count),
            )
            .where(
                ScanListing.scan_id == scan_id,
                ScanListing.initial_view_count.is_not(None),
            )
            .group_by(ScanListing.captured_at)
        )

    merged: dict[datetime, tuple[int, int]] = {}
    for dt, count, total in baseline_result.all():
        merged[dt] = (int(count or 0), int(total or 0))
    # Real v2.9 rounds win on identical timestamps.
    for dt, count, total in live_result.all():
        merged[dt] = (int(count or 0), int(total or 0))
    ordered = sorted(merged.items(), key=lambda item: item[0], reverse=True)[:limit]
    return [(dt, count, total) for dt, (count, total) in ordered]


async def get_scan_growth_rows(
    scan_id: int, period_hours: int, category_key: str | None = None
) -> tuple[list[GrowthMetric], int]:
    """Return TOP growth, sorted by real absolute view increase.

    If an automatic checkpoint exists for the requested horizon, compare it to
    the initial scan snapshot. Otherwise fall back to the closest manual history
    so old scans remain useful.
    """
    period_hours = period_hours if period_hours in set(OBSERVATION_HOURS) else 1
    pairs = await get_scan_rows(scan_id)
    if category_key:
        pairs = [p for p in pairs if p[0].category_key == category_key]
    listings = {listing.external_id: listing for listing, _ in pairs}
    if not listings:
        return [], 0

    async with SessionLocal() as session:
        result = await session.execute(
            select(
                ScanViewHistory.external_id,
                ScanViewHistory.view_count,
                ScanViewHistory.recorded_at,
                ScanViewHistory.target_hours,
            )
            .where(
                ScanViewHistory.scan_id == scan_id,
                ScanViewHistory.external_id.in_(list(listings)),
            )
            .order_by(ScanViewHistory.external_id, ScanViewHistory.recorded_at)
        )
        points = list(result.all())

    baseline: dict[str, tuple[datetime, int]] = {}
    for listing, snap in pairs:
        if snap.initial_view_count is not None:
            baseline[listing.external_id] = (snap.captured_at, int(snap.initial_view_count))

    rounds = {snap.captured_at for _, snap in pairs if snap.initial_view_count is not None}
    for _, _, recorded_at, _ in points:
        rounds.add(recorded_at)

    # Prefer the exact scheduled checkpoint for +N hours.
    exact: dict[str, tuple[datetime, int]] = {}
    for external_id, view_count, recorded_at, target_hours in points:
        if target_hours == period_hours:
            exact[external_id] = (recorded_at, int(view_count))

    growth: list[GrowthMetric] = []
    if exact:
        for external_id, (current_at, current_views) in exact.items():
            base = baseline.get(external_id)
            listing = listings.get(external_id)
            if base is None or listing is None:
                continue
            base_at, base_views = base
            elapsed_hours = (current_at - base_at).total_seconds() / 3600
            if elapsed_hours <= 0:
                continue
            delta = current_views - base_views
            if delta <= 0:
                continue
            growth.append(GrowthMetric(
                listing=listing, base_views=base_views, current_views=current_views,
                delta=delta, elapsed_hours=elapsed_hours, per_hour=delta / elapsed_hours,
                observed_at=current_at,
            ))
    else:
        # Compatibility fallback for manual v2.9/v3.0 snapshots.
        by_id: dict[str, list[tuple[datetime, int]]] = {}
        for external_id, (base_at, base_views) in baseline.items():
            by_id.setdefault(external_id, []).append((base_at, base_views))
        for external_id, view_count, recorded_at, _ in points:
            point = (recorded_at, int(view_count))
            series = by_id.setdefault(external_id, [])
            if point not in series:
                series.append(point)
        for external_id, series in by_id.items():
            series.sort(key=lambda point: point[0])
            if len(series) < 2:
                continue
            current_at, current_views = series[-1]
            target_at = current_at - timedelta(hours=period_hours)
            before = [point for point in series[:-1] if point[0] <= target_at]
            base_at, base_views = before[-1] if before else series[0]
            elapsed_hours = (current_at - base_at).total_seconds() / 3600
            if elapsed_hours < (2 / 60):
                continue
            delta = current_views - base_views
            listing = listings.get(external_id)
            if delta <= 0 or listing is None:
                continue
            growth.append(GrowthMetric(
                listing=listing, base_views=base_views, current_views=current_views,
                delta=delta, elapsed_hours=elapsed_hours, per_hour=delta / elapsed_hours,
                observed_at=current_at,
            ))

    # User requested the ranking by actual added views, not by tiny-window velocity.
    growth.sort(key=lambda item: (item.delta, item.per_hour, item.current_views), reverse=True)
    return growth[:GROWTH_TOP_LIMIT], len(rounds)


def my_scans_keyboard(scans: list[UserScan]) -> InlineKeyboardMarkup:
    rows = []
    for scan in scans[:8]:
        icon = (
            "✅" if scan.status == "done"
            else "⚠️" if scan.status == "partial"
            else "⏳" if scan.status in {"queued", "running"}
            else "⚪️"
        )
        target_label = _date_label(scan.target_date) if scan.target_date else _moscow_text(scan.finished_at or scan.created_at)[:10]
        label = f"{icon} {scan.title[:22]} · {target_label[:5]}"
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


def _identity_kwargs(title: str, category: str) -> tuple[ProductIdentity, dict]:
    identity = recognize_product(title, category)
    values = {
        "identity_key": identity.key or None,
        "identity_label": identity.label or None,
        "identity_brand": identity.brand or None,
        "identity_model": identity.model or None,
        "identity_variant": identity.variant or None,
        "identity_product_type": identity.product_type or None,
        "identity_storage_gb": identity.storage_gb,
        "identity_ram_gb": identity.ram_gb,
        "identity_specs": identity.specs or None,
        "identity_confidence": identity.confidence,
    }
    return identity, values


def _apply_identity(row: Listing, title: str, category: str) -> ProductIdentity:
    identity, values = _identity_kwargs(title, category)
    for field, value in values.items():
        setattr(row, field, value)
    return identity


def _identity_display(row: Listing) -> str:
    if (row.identity_confidence or 0) >= 70 and row.identity_label:
        return row.identity_label
    return row.title


async def backfill_product_identities() -> int:
    """Fill v3.0 identity fields for listings collected by older versions."""
    async with db_write_lock:
        async with SessionLocal() as session:
            result = await session.execute(select(Listing).where(Listing.identity_confidence.is_(None)))
            rows = list(result.scalars().all())
            for row in rows:
                _apply_identity(row, row.title, row.category)
            if rows:
                await session.commit()
            return len(rows)


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
                _identity, identity_values = _identity_kwargs(item.title, category_name)
                session.add(Listing(
                    external_id=item.external_id, category_key=category_key, category=category_name,
                    title=item.title, price_text=item.price_text, price_eur=item.price_eur,
                    posted_text=item.posted_text, posted_date_msk=(posted_date_moscow(item.posted_text).isoformat() if posted_date_moscow(item.posted_text) else None),
                    url=item.url, first_seen_at=now, last_seen_at=now,
                    is_active=True, disappeared_at=None,
                    **identity_values,
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
                _apply_identity(row, item.title, category_name)
                row.posted_text = item.posted_text
                parsed_day = posted_date_moscow(item.posted_text)
                row.posted_date_msk = parsed_day.isoformat() if parsed_day else row.posted_date_msk
                row.url = item.url
                row.last_seen_at = now
                row.is_active = True
                row.disappeared_at = None
        await session.commit()
        return new_items, len(unique) - len(new_items), enriched_count


def berlin_today_utc_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(MOSCOW)
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


def _moscow_text(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")


def _berlin_text(dt: datetime | None) -> str:
    # Backward-compatible helper name; all user-facing timestamps are Moscow time.
    return _moscow_text(dt)


def _moscow_today_iso() -> str:
    return datetime.now(MOSCOW).date().isoformat()


def _date_label(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return value


def _parse_scan_date_input(text: str | None) -> str | None:
    raw = (text or "").strip().replace("/", ".").replace("-", ".")
    today = datetime.now(MOSCOW).date()
    try:
        if re.fullmatch(r"\d{1,2}", raw):
            day = int(raw)
            value = today.replace(day=day)
        elif re.fullmatch(r"\d{1,2}\.\d{1,2}", raw):
            day, month = map(int, raw.split("."))
            value = datetime(today.year, month, day).date()
        elif re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", raw):
            value = datetime.strptime(raw, "%d.%m.%Y").date()
        else:
            return None
    except ValueError:
        return None
    if value > today:
        return None
    return value.isoformat()


def write_listing_csv(rows: list[Listing], mode: str) -> Path:
    now = datetime.now(MOSCOW)
    path, writer, f = _temp_csv(f"kleinanzeigen_{mode}_{now:%Y-%m-%d_%H-%M}.csv")
    try:
        writer.writerow([
            "Категория", "Название", "🧠 Распознанный товар", "Бренд", "Модель", "Версия",
            "Память, GB", "RAM, GB", "Точность распознавания, %",
            "Цена", "Цена, €", "👁 Просмотры", "Дата (МСК)", "Как показано на Kleinanzeigen", "Ссылка"
        ])
        for row in rows:
            writer.writerow([
                row.category, row.title, row.identity_label or "", row.identity_brand or "",
                row.identity_model or "", row.identity_variant or "",
                row.identity_storage_gb if row.identity_storage_gb is not None else "",
                row.identity_ram_gb if row.identity_ram_gb is not None else "",
                row.identity_confidence if row.identity_confidence is not None else "",
                _price_display(row.price_text, row.price_eur),
                row.price_eur if row.price_eur is not None else "",
                row.view_count if row.view_count is not None else "",
                _date_label(row.posted_date_msk), row.posted_text or "", row.url,
            ])
    finally:
        f.close()
    return path


def write_frequent_csv(rows) -> Path:
    now = datetime.now(MOSCOW)
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
    now = datetime.now(MOSCOW)
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
    now = datetime.now(MOSCOW)
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
    now = datetime.now(MOSCOW)
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
        traffic_priority="scan_inline",
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


async def refresh_view_counts(
    rows: list[Listing], message: Message | BotChatAdapter | None = None, *,
    force: bool = False, max_age_seconds: int | None = None,
    traffic_priority: str = "manual",
) -> tuple[int, int, int]:
    """Refresh missing/stale public view counters and persist them.

    Returns (requested, updated, failed). max_age_seconds lets automatic checkpoints
    safely reuse a counter fetched only a few minutes ago by another scan/user.
    """
    if not rows:
        return 0, 0, 0

    effective_ttl = VIEW_COUNT_CACHE_TTL_SECONDS if max_age_seconds is None else max(0, int(max_age_seconds))
    cutoff = datetime.utcnow() - timedelta(seconds=effective_ttl)
    targets = [
        row for row in rows
        if row.url and (force or row.views_checked_at is None or row.views_checked_at < cutoff)
    ]
    if not targets:
        return 0, 0, 0

    status = None
    status_note = "свежий запрос" if force else f"кэш {max(1, effective_ttl // 60)} мин."
    if message is not None:
        try:
            status = await message.answer(
                f"👁 Собираю просмотры для <b>{len(targets)}</b> объявлений…\n"
                f"⚡ Прямой счётчик + browser fallback · {status_note}",
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
                    f"⚡ Прямой счётчик + browser fallback · {status_note}",
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
            traffic_priority=traffic_priority,
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


async def claim_due_observation() -> ScanObservation | None:
    """Claim one due automatic checkpoint and quickly discard stale missed ones."""
    for _ in range(100):
        now = datetime.utcnow()
        async with db_write_lock:
            async with SessionLocal() as session:
                result = await session.execute(
                    select(ScanObservation)
                    .where(
                        ScanObservation.status == "pending",
                        ScanObservation.due_at <= now,
                    )
                    .order_by(ScanObservation.due_at.asc())
                    .limit(1)
                )
                obs = result.scalar_one_or_none()
                if obs is None:
                    return None
                if now - obs.due_at > timedelta(minutes=OBSERVATION_LATE_GRACE_MINUTES):
                    obs.status = "missed"
                    obs.completed_at = now
                    obs.error_text = "checkpoint missed while service was offline/busy"
                    await session.commit()
                    continue
                obs.status = "running"
                obs.started_at = now
                await session.commit()
                await session.refresh(obs)
                session.expunge(obs)
                return obs
    return None


async def mark_observation_result(
    observation_id: int, *, status: str, item_count: int = 0, error_text: str | None = None
) -> None:
    async with db_write_lock:
        async with SessionLocal() as session:
            obs = await session.get(ScanObservation, observation_id)
            if obs is None:
                return
            obs.status = status
            obs.completed_at = datetime.utcnow()
            obs.item_count = item_count
            obs.error_text = (error_text or "")[:1000] or None
            await session.commit()


async def recover_running_observations() -> int:
    """Requeue observations left in running state by an interrupted Railway process."""
    cutoff = datetime.utcnow() - timedelta(minutes=15)
    changed = 0
    async with db_write_lock:
        async with SessionLocal() as session:
            result = await session.execute(
                select(ScanObservation).where(
                    ScanObservation.status == "running",
                    ScanObservation.started_at.is_not(None),
                    ScanObservation.started_at < cutoff,
                )
            )
            for obs in result.scalars().all():
                obs.status = "pending"
                obs.started_at = None
                changed += 1
            await session.commit()
    return changed


async def process_observation(bot: Bot, obs: ScanObservation) -> None:
    async with SessionLocal() as session:
        scan = await session.get(UserScan, obs.scan_id)
    if scan is None or scan.status not in {"done", "partial"}:
        await mark_observation_result(obs.id, status="error", error_text="scan not available")
        return

    pairs = await get_scan_rows(scan.id)
    rows = [row for row, _ in pairs]
    if not rows:
        await mark_observation_result(obs.id, status="done", item_count=0)
        return

    try:
        requested, updated, failed = await refresh_view_counts(rows, None, force=False, max_age_seconds=300, traffic_priority="background")
        recorded = await update_scan_view_refresh(scan.id, target_hours=obs.target_hours)
        if recorded <= 0:
            await mark_observation_result(
                obs.id, status="error", item_count=0,
                error_text=f"no fresh view values; failures={failed}",
            )
            log.warning("Observation produced no fresh counters scan=%s +%sh", scan.id, obs.target_hours)
            return
        await mark_observation_result(
            obs.id, status="done", item_count=recorded,
            error_text=(f"view failures: {failed}" if failed else None),
        )
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔥 Открыть популярное", callback_data="popular_now")],
                [InlineKeyboardButton(text="📊 Открыть скан", callback_data=f"scan:{scan.id}")],
            ])
            await bot.send_message(
                scan.user_id,
                f"✅ <b>Контрольный замер +{obs.target_hours}ч готов</b>\n\n"
                f"Скан: <b>{html.escape(scan.title)}</b>\n"
                f"📅 Дата объявлений: <b>{_date_label(scan.target_date)}</b>\n"
                f"👁 Свежих значений сохранено: <b>{recorded}</b>\n\n"
                "Теперь в «🔥 Популярное сейчас» доступен TOP роста по каждой категории отдельно.",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception:
            log.debug("Could not notify user about observation scan=%s +%sh", scan.id, obs.target_hours, exc_info=True)
        log.info(
            "Observation done scan=%s +%sh requested=%s updated=%s recorded=%s failed=%s",
            scan.id, obs.target_hours, requested, updated, recorded, failed,
        )
    except Exception as exc:
        log.exception("Automatic observation failed scan=%s +%sh", scan.id, obs.target_hours)
        await mark_observation_result(obs.id, status="error", error_text=str(exc))


async def observation_scheduler(bot: Bot, worker_id: int = 1) -> None:
    """Persistent +1/+3/+6/+12/+24h view-checkpoint worker."""
    while True:
        try:
            obs = await claim_due_observation()
            if obs is None:
                await asyncio.sleep(OBSERVATION_POLL_SECONDS)
                continue
            await process_observation(bot, obs)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Observation scheduler loop error")
            await asyncio.sleep(OBSERVATION_POLL_SECONDS)


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
        all_rows, period=("today" if rows_override is not None else s.period), price_filter=s.price_filter, clean_noise=s.clean_noise,
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
        "Скан по дате: <b>дата + глубина 25 / 50 / 100</b>\n"
        f"Ключевые слова: <b>{include}</b>\n"
        f"Исключить: <b>{exclude}</b>\n\n"
        "<i>Точный поиск по дате сначала быстро находит начало выбранного дня, а затем применяет выбранную глубину 25 / 50 / 100 страниц. Внутренняя очередь, кэш и совместные сканы скрыты от пользователей. "
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
        avg_quality = (await session.execute(select(func.coalesce(func.avg(ParserRun.quality_score), 0)).where(
            ParserRun.started_at >= start_utc, ParserRun.started_at < end_utc, ParserRun.success.is_(True),
            ParserRun.quality_score > 0,
        ))).scalar_one()
        missing_dates = (await session.execute(select(func.coalesce(func.sum(ParserRun.missing_date_count), 0)).where(
            ParserRun.started_at >= start_utc, ParserRun.started_at < end_utc, ParserRun.success.is_(True),
        ))).scalar_one()
        invalid_pages = (await session.execute(select(func.coalesce(func.sum(ParserRun.invalid_pages), 0)).where(
            ParserRun.started_at >= start_utc, ParserRun.started_at < end_utc, ParserRun.success.is_(True),
        ))).scalar_one()
        repeated_pages = (await session.execute(select(func.coalesce(func.sum(ParserRun.repeated_pages), 0)).where(
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
        f"<b>🛡 v3.1 качество сегодня</b>\n"
        f"Запусков категорий: <b>{runs}</b>\n"
        f"Среднее качество: <b>{round(float(avg_quality or 0))}/100</b>\n"
        f"Дат не распознано: <b>{missing_dates}</b>\n"
        f"Невалидных страниц: <b>{invalid_pages}</b>\n"
        f"Повторов страниц: <b>{repeated_pages}</b>\n"
        f"Сетевых страниц: <b>{pages}</b>\n"
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
    date_complete: bool = False
    oldest_date_seen: str = ""
    max_page_reached: int = 0
    matched_ids: list[str] | None = None
    # v3.1 quality telemetry. These fields are intentionally part of the shared
    # ScanResult so cached/shared scans preserve the same reliability verdict.
    cards_seen: int = 0
    listings_parsed: int = 0
    missing_date_count: int = 0
    missing_price_count: int = 0
    promoted_filtered: int = 0
    duplicate_count: int = 0
    invalid_pages: int = 0
    repeated_pages: int = 0
    low_quality_pages: int = 0
    verified_pages: int = 0
    view_failures: int = 0
    quality_score: int = 0
    quality_note: str = ""


def _calculate_scan_quality(
    *,
    listings_parsed: int,
    missing_dates: int,
    missing_prices: int,
    invalid_pages: int,
    repeated_pages: int,
    low_quality_pages: int,
    verified_pages: int,
    pages_scanned: int,
    view_failures: int,
    date_complete: bool,
) -> tuple[int, str]:
    """Return a conservative 0-100 quality score plus one compact reason."""
    score = 100.0
    notes: list[str] = []
    if listings_parsed > 0:
        date_cov = max(0.0, min(1.0, (listings_parsed - missing_dates) / listings_parsed))
        price_cov = max(0.0, min(1.0, (listings_parsed - missing_prices) / listings_parsed))
        score -= (1.0 - date_cov) * 35.0
        score -= (1.0 - price_cov) * 8.0
        if date_cov < 0.80:
            notes.append(f"дат распознано {round(date_cov * 100)}%")
    elif date_complete:
        # A verified empty day can still be a valid scan.
        notes.append("объявлений за дату не найдено")

    score -= min(30.0, invalid_pages * 10.0)
    score -= min(25.0, repeated_pages * 12.0)
    score -= min(20.0, low_quality_pages * 4.0)
    if pages_scanned and verified_pages == 0:
        score -= 10.0
        notes.append("страницы слабо подтверждены")
    if invalid_pages:
        notes.append(f"невалидных страниц {invalid_pages}")
    if repeated_pages:
        notes.append(f"повторов страниц {repeated_pages}")
    if view_failures:
        # Views are secondary data: a few failures should not make the category
        # parser look broken, but a large number is still worth surfacing.
        score -= min(8.0, view_failures * 0.15)
    if not date_complete:
        score = min(score, 69.0)
        if listings_parsed == 0:
            score = min(score, 45.0)
        notes.append("охват даты не подтверждён")

    final = max(0, min(100, int(round(score))))
    if not notes:
        notes.append("проверки пройдены")
    return final, "; ".join(notes[:3])


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
    target_date: str = ""
    incomplete_categories: int = 0
    scan_notes: list[str] | None = None
    matched_ids: set[str] | None = None
    quality_scores: list[int] | None = None
    quality_notes: list[str] | None = None


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
    oldest_date_seen: str = ""
    current_page_date: str = ""
    phase: str = "seeking"
    segment_name: str = ""
    segments_done: int = 0
    segments_total: int = 0
    collection_index: int = 0
    collection_start_page: int = 0
    date_coverage_pct: int = 0
    quality_score: int = 100
    quality_warning: str = ""


category_live_progress: dict[str, CategoryLiveProgress] = {}


def _date_scan_limit(target_date: str) -> int:
    """Verified public page window for one Kleinanzeigen result feed."""
    return PUBLIC_SEARCH_PAGE_CAP if target_date else MAX_PAGES_PER_CATEGORY


def _progress_key(category_key: str, target_date: str, page_limit: int | None = None) -> str:
    depth = int(page_limit or 0)
    return f"{category_key}:date:{target_date}:depth:{depth}"


scan_queue: asyncio.Queue[ScanJob] = asyncio.Queue()
active_jobs: dict[int, ScanJob] = {}
queued_job_ids: list[str] = []
job_guard = asyncio.Lock()
category_inflight: dict[str, asyncio.Task[ScanResult]] = {}
category_inflight_guard = asyncio.Lock()
# Exact-date cache must preserve the exact 25/50/100-page result set, so v3.0.6
# caches ScanResult (including matched IDs) in memory instead of reconstructing a
# result from every listing ever seen for that date.
category_result_cache: dict[str, tuple[float, ScanResult]] = {}
db_write_lock = asyncio.Lock()


def berlin_date_key() -> str:
    return datetime.now(BERLIN).date().isoformat()


async def get_category_scan_state(category_key: str) -> CategoryScanState | None:
    async with SessionLocal() as session:
        return await session.get(CategoryScanState, category_key)


async def save_category_scan_state(
    category_key: str,
    *,
    target_date: str,
    mode: str,
    pages_scanned: int,
    new_count: int,
    today_seen: int,
    reason: str,
    head_ids: list[str],
    seed_complete: bool,
    seed_capped: bool,
    coverage_pages: int | None = None,
) -> CategoryScanState:
    day_key = berlin_date_key()
    async with SessionLocal() as session:
        state = await session.get(CategoryScanState, category_key)
        if state is None:
            state = CategoryScanState(category_key=category_key, scan_date=day_key)
            session.add(state)
        new_day = state.scan_date != day_key or (state.target_date or "") != target_date
        if new_day:
            state.scan_date = day_key
            state.target_date = target_date
            state.total_runs = 0
            state.day_seed_complete = False
            state.day_seed_capped = False
            state.day_full_pages = 0
            state.head_ids = ""

        if head_ids:
            state.head_ids = ",".join(head_ids[:INCREMENTAL_HEAD_SIZE])
        state.target_date = target_date
        state.last_scan_at = datetime.utcnow()
        state.last_mode = mode
        state.last_pages = pages_scanned
        state.last_new = new_count
        state.last_today_seen = today_seen
        state.last_stop_reason = reason[:255]
        state.total_runs = (state.total_runs or 0) + 1
        if mode in {"full", "date"}:
            # Keep the deepest seeded window for the day. A 25-page seed enables
            # later 25-page fast scans, while a later 100-page request can deepen it.
            state.day_full_pages = max(state.day_full_pages or 0, int(coverage_pages or pages_scanned))
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
            cards_seen=result.cards_seen,
            listings_parsed=result.listings_parsed,
            missing_date_count=result.missing_date_count,
            missing_price_count=result.missing_price_count,
            promoted_filtered=result.promoted_filtered,
            duplicate_count=result.duplicate_count,
            invalid_pages=result.invalid_pages,
            repeated_pages=result.repeated_pages,
            low_quality_pages=result.low_quality_pages,
            view_failures=result.view_failures,
            quality_score=result.quality_score,
            stop_reason=result.reason[:255],
            success=success,
            error_text=(error_text[:1000] if error_text else None),
        ))
        await session.commit()


async def scan_one_category(parser: KleinanzeigenParser, cat, user_id: int, page_limit: int, target_date: str) -> ScanResult:
    """Reliably locate the selected Moscow date and collect 25/50/100-page depth.

    v3.1 treats page identity and publication-date coverage as data-quality signals.
    A weak/normalized/repeated page may contribute diagnostics, but it is never used
    as proof that a date is absent. This prevents a silent parser degradation from
    turning into a believable zero-result scan.
    """
    depth = page_limit if page_limit in PAGE_LIMIT_CHOICES else 50
    target_day = datetime.strptime(target_date, "%Y-%m-%d").date()
    progress_key = _progress_key(cat.key, target_date, depth)
    mode = "date"

    category_live_progress[progress_key] = CategoryLiveProgress(
        category_key=cat.key,
        category_name=cat.name,
        mode=mode,
        estimated_pages=depth,
        started_monotonic=time.monotonic(),
        page_limit=depth,
        phase="jumping",
    )

    new_count = 0
    today_seen = 0
    known_total = 0
    enriched_total = 0
    target_seen_any = False
    request_complete = False
    oldest_date_seen = ""
    first_page_head_ids: list[str] = []
    processed_target_ids: set[str] = set()
    started_at = datetime.utcnow()
    reason = ""
    hit_limit = False
    collection_start_page = 0
    direct_pages_collected = 0
    network_requests = 0
    max_page_reached = 0

    # v3.1 quality telemetry. Counters increase only for actual network responses,
    # never when a page is reused from the locator's in-memory cache.
    cards_seen = 0
    listings_parsed = 0
    missing_date_count = 0
    missing_price_count = 0
    promoted_filtered = 0
    duplicate_count = 0
    invalid_pages = 0
    repeated_pages = 0
    low_quality_pages = 0
    verified_pages = 0
    view_failures = 0

    def classify(items):
        profile = profile_page_dates(items, target_day)
        return profile.relation, profile.pairs, profile.days, profile

    def update_quality_live(note: str = "") -> None:
        live = category_live_progress.get(progress_key)
        if live is None:
            return
        if listings_parsed:
            coverage = max(0.0, min(1.0, (listings_parsed - missing_date_count) / listings_parsed))
            live.date_coverage_pct = round(coverage * 100)
        rough, rough_note = _calculate_scan_quality(
            listings_parsed=listings_parsed,
            missing_dates=missing_date_count,
            missing_prices=missing_price_count,
            invalid_pages=invalid_pages,
            repeated_pages=repeated_pages,
            low_quality_pages=low_quality_pages,
            verified_pages=verified_pages,
            pages_scanned=network_requests,
            view_failures=view_failures,
            date_complete=True,
        )
        live.quality_score = rough
        live.quality_warning = note or (rough_note if rough < 85 else "")

    def update_live(page: int, days: list, phase: str, collection_index: int | None = None) -> None:
        nonlocal oldest_date_seen, max_page_reached
        max_page_reached = max(max_page_reached, int(page or 0))
        page_date_hint = ""
        if days:
            page_oldest = min(days)
            page_newest = max(days)
            page_date_hint = page_oldest.isoformat() if page_oldest == page_newest else f"{page_newest.isoformat()}..{page_oldest.isoformat()}"
            if not oldest_date_seen or page_oldest.isoformat() < oldest_date_seen:
                oldest_date_seen = page_oldest.isoformat()
        live = category_live_progress.get(progress_key)
        if live is not None:
            live.page = page
            live.oldest_date_seen = oldest_date_seen
            live.current_page_date = page_date_hint
            live.phase = phase
            if collection_index is not None:
                live.collection_index = min(depth, max(0, collection_index))
                live.page_limit = depth
        update_quality_live()

    async def process_target_items(items, pairs, limit: int | None = None) -> int:
        nonlocal new_count, today_seen, known_total, enriched_total, target_seen_any, first_page_head_ids, view_failures
        target_items = [
            item for item, item_day in pairs
            if item_day == target_day and item.external_id not in processed_target_ids
        ]
        if limit is not None:
            target_items = target_items[:max(0, int(limit))]
        if not target_items:
            return 0
        processed_target_ids.update(item.external_id for item in target_items)
        target_seen_any = True
        today_seen += len(target_items)
        if not first_page_head_ids:
            first_page_head_ids = [item.external_id for item in target_items[:INCREMENTAL_HEAD_SIZE]]
        async with db_write_lock:
            new_items, known_count, enriched_count = await upsert_page_items(cat.key, cat.name, target_items)
        live = category_live_progress.get(progress_key)
        _, _, failed_views = await enrich_page_view_counts(parser, target_items, live)
        view_failures += failed_views
        new_count += len(new_items)
        known_total += known_count
        enriched_total += enriched_count
        live = category_live_progress.get(progress_key)
        if live is not None:
            live.today_seen = today_seen
            live.new_count = new_count
            live.known_count = known_total
        update_quality_live()
        return len(target_items)

    async def locate_feed(base_url: str, feed_name: str):
        """Locate the first target-date page inside one verified <=50-page feed."""
        nonlocal network_requests, cards_seen, listings_parsed, missing_date_count
        nonlocal missing_price_count, promoted_filtered, duplicate_count, invalid_pages
        nonlocal repeated_pages, low_quality_pages, verified_pages
        cache: dict[int, object] = {}
        fingerprints: dict[str, int] = {}
        effective_limit = PUBLIC_SEARCH_PAGE_CAP
        site_max_page: int | None = None
        discovered_shards: list[tuple[str, int | None]] = []
        invalid_note = ""

        def locator_result(status: str, reason_text: str = "", candidate_page: int | None = None):
            return {
                "status": status, "reason": reason_text, "fetch": fetch,
                "limit": effective_limit, "site_max_page": site_max_page,
                "candidate": candidate_page, "shards": list(discovered_shards),
            }

        async def fetch(page: int, phase: str):
            nonlocal network_requests, effective_limit, site_max_page, discovered_shards, invalid_note
            nonlocal cards_seen, listings_parsed, missing_date_count, missing_price_count
            nonlocal promoted_filtered, duplicate_count, invalid_pages, repeated_pages
            nonlocal low_quality_pages, verified_pages
            page = max(1, min(effective_limit, int(page)))
            fresh = page not in cache
            if not fresh:
                info = cache[page]
            else:
                info = await parser.parse_category_page_info(page_url(base_url, page), page)
                cache[page] = info
                network_requests += 1
                cards_seen += int(getattr(info, "raw_candidates", 0) or 0)
                listings_parsed += len(info.items)
                missing_date_count += int(getattr(info, "missing_date_count", 0) or 0)
                missing_price_count += int(getattr(info, "missing_price_count", 0) or 0)
                promoted_filtered += int(getattr(info, "promoted_filtered", 0) or 0)
                duplicate_count += int(getattr(info, "duplicate_cards", 0) or 0)
                if bool(getattr(info, "page_verified", False)):
                    verified_pages += 1
                if getattr(info, "max_page", None):
                    site_max_page = max(1, int(info.max_page))
                    effective_limit = max(1, min(PUBLIC_SEARCH_PAGE_CAP, site_max_page))
                if page == 1 and getattr(info, "location_shards", None):
                    discovered_shards = list(info.location_shards or [])
                if phase == "jumping" and DATE_JUMP_PROBE_DELAY_SECONDS:
                    await asyncio.sleep(DATE_JUMP_PROBE_DELAY_SECONDS)

            items = info.items
            relation, pairs, days, profile = classify(items)
            valid = bool(getattr(info, "request_matches_page", True)) and not bool(getattr(info, "suspicious", False))
            fp = getattr(info, "fingerprint", "") or ""
            repeated = False
            if fp:
                previous = fingerprints.get(fp)
                if previous is None:
                    fingerprints[fp] = page
                elif previous != page and len(items) >= 5:
                    valid = False
                    repeated = True
                    invalid_note = f"страница {page} повторила содержимое страницы {previous}"
            if fresh and repeated:
                repeated_pages += 1
            if fresh and not valid:
                invalid_pages += 1
            if fresh and items and relation == "unknown":
                low_quality_pages += 1

            if not valid:
                relation, pairs, days = "invalid", [], []
                invalid_note = invalid_note or f"страница {page} была нормализована/не подтверждена сайтом"
            update_live(page, days, phase)
            if relation == "unknown":
                update_quality_live(f"не хватает дат на странице {page}")
            elif relation == "invalid":
                update_quality_live(invalid_note)
            log.info(
                "category=%s feed=%s phase=%s page=%s relation=%s actual=%s max=%s verified=%s "
                "date_cov=%.0f%% parsed=%s missing_date=%s raw=%s promoted=%s duplicates=%s valid=%s requests=%s",
                cat.name, feed_name, phase, page, relation,
                getattr(info, "actual_page", None), getattr(info, "max_page", None),
                getattr(info, "page_verified", False), float(getattr(info, "date_coverage", 0.0) or 0.0) * 100,
                len(items), getattr(info, "missing_date_count", 0), getattr(info, "raw_candidates", 0),
                getattr(info, "promoted_filtered", 0), getattr(info, "duplicate_cards", 0), valid, network_requests,
            )
            return items, relation, pairs, days

        low_newer = 0
        high: int | None = None
        probe = 1
        while True:
            items, relation, pairs, days = await fetch(probe, "jumping")
            if relation in {"target", "older", "mixed", "empty"}:
                high = probe
                break
            if relation == "invalid":
                return locator_result("invalid", invalid_note)
            if relation == "unknown":
                # Unknown chronology is not a valid jump signal. Nearby pages may be
                # healthier, but if the current probe is page 1 we cannot safely infer
                # a direction and therefore return a partial result instead of zero.
                return locator_result("unknown", "publication dates could not be verified")
            if relation == "newer":
                low_newer = probe
            if probe >= effective_limit:
                return locator_result("too_deep", "target beyond public page window")
            next_probe = min(effective_limit, 2 if probe == 1 else probe * 2)
            if next_probe == probe:
                return locator_result("too_deep", "target beyond public page window")
            probe = next_probe

        lo = max(1, low_newer + 1)
        hi = high
        while lo < hi:
            mid = (lo + hi) // 2
            items, relation, pairs, days = await fetch(mid, "jumping")
            if relation == "invalid":
                return locator_result("invalid", invalid_note)
            if relation == "unknown":
                return locator_result("unknown", "weak date coverage near boundary")
            if relation == "newer":
                lo = mid + 1
            else:
                hi = mid
        boundary = lo

        candidate = None
        saw_newer = saw_older = saw_unknown = False
        for page in range(max(1, boundary - 3), min(effective_limit, boundary + 5) + 1):
            items, relation, pairs, days = await fetch(page, "jumping")
            if relation == "target":
                candidate = page
                break
            if relation == "newer":
                saw_newer = True
            elif relation in {"older", "mixed", "empty"}:
                saw_older = True
            elif relation == "unknown":
                saw_unknown = True
            elif relation == "invalid":
                return locator_result("invalid", invalid_note)

        if candidate is not None:
            # Walk back a few pages so the first boundary card cannot be missed.
            for back in range(candidate - 1, max(0, candidate - 4), -1):
                items, relation, pairs, days = await fetch(back, "jumping")
                if relation == "target":
                    candidate = back
                elif relation == "newer":
                    break
                elif relation in {"unknown", "invalid"}:
                    return locator_result("unknown", "could not verify page immediately before target")
                else:
                    break
            return locator_result("found", candidate_page=candidate)

        # A zero is allowed only when the *whole* feed is visible inside the public
        # page window. For a large feed (>50 pages), a local crossing is not enough
        # evidence to conclude that an entire category has zero listings on that day;
        # we must split this category into smaller official location feeds first.
        full_feed_visible = site_max_page is not None and site_max_page <= effective_limit
        if saw_newer and saw_older and not saw_unknown:
            if full_feed_visible:
                return locator_result("absent", "verified date crossing without target listings")
            return locator_result("ambiguous_absent", "large feed requires independent sub-feed verification")
        if saw_older and low_newer == 0 and not saw_unknown:
            if full_feed_visible:
                return locator_result("absent", "feed starts after the selected calendar day")
            return locator_result("ambiguous_absent", "large feed requires independent sub-feed verification")
        return locator_result("unknown", "could not verify date boundary")

    async def collect_direct(locator) -> tuple[str, int]:
        """Collect literal nationwide pages while they remain verified."""
        nonlocal direct_pages_collected, collection_start_page, request_complete, reason, hit_limit
        candidate = int(locator["candidate"])
        limit = int(locator["limit"])
        fetch = locator["fetch"]
        collection_start_page = candidate
        live = category_live_progress.get(progress_key)
        if live is not None:
            live.phase = "collecting"
            live.collection_start_page = candidate
            live.collection_index = 0
        for index in range(1, depth + 1):
            page = candidate + index - 1
            if page > limit:
                hit_limit = True
                return "needs_hidden", direct_pages_collected
            items, relation, pairs, days = await fetch(page, "collecting")
            if relation in {"invalid", "unknown"}:
                hit_limit = True
                return "needs_hidden", direct_pages_collected
            if relation == "empty":
                request_complete = True
                reason = "выдача закончилась раньше выбранной глубины"
                return "done", direct_pages_collected
            if relation == "older" or (relation == "mixed" and not any(d == target_day for d in days)):
                request_complete = True
                reason = "выбранная дата закончилась раньше выбранной глубины" if target_seen_any else "выбранная дата пройдена; объявлений за неё не найдено"
                return "done", direct_pages_collected
            direct_pages_collected += 1
            update_live(page, days, "collecting", direct_pages_collected)
            await process_target_items(items, pairs)
            if direct_pages_collected >= depth:
                request_complete = True
                reason = f"собрано {depth} страниц от начала выбранной даты"
                return "done", direct_pages_collected
            if PAGE_DELAY_SECONDS:
                await asyncio.sleep(min(PAGE_DELAY_SECONDS, 0.25))
        return "done", direct_pages_collected

    async def hidden_fill(remaining_virtual_pages: int) -> tuple[bool, bool]:
        """Fill remaining depth from independent location feeds.

        v3.1.3 keeps the multi-category false-zero fix and adds resilient 403 recovery. Every category owns its own
        locator state. If a state feed is itself larger than Kleinanzeigen's public
        50-page window, it is recursively split into smaller official location feeds
        discovered from that category page. Nothing from the previous selected
        category is reused.
        """
        nonlocal request_complete, reason, hit_limit
        if remaining_virtual_pages <= 0:
            return True, False

        start_count = today_seen
        goal = start_count + remaining_virtual_pages * 25
        unresolved = False
        visited: set[str] = set()
        max_hidden_feeds = 180
        max_shard_depth = 2
        feeds_processed = 0

        live = category_live_progress.get(progress_key)
        if live is not None:
            live.phase = "jumping"
            live.collection_start_page = 0
            live.segment_name = ""
            live.segments_done = 0
            live.segments_total = 0

        queue: list[tuple[str, str, int]] = [
            (state_name, _regional_category_url(cat.url, slug, location_id), 0)
            for state_name, slug, location_id in GERMAN_STATE_SEGMENTS
        ]

        def add_children(parent_name: str, loc: dict, level: int) -> bool:
            if level >= max_shard_depth:
                return False
            children = list(loc.get("shards") or [])
            if not children:
                return False
            added = 0
            # Prefer smaller counted feeds; they are more likely to expose the
            # requested historical date within the public 50-page window.
            children.sort(key=lambda item: (item[1] is None, item[1] or 10**12, item[0]))
            for child_url, child_count in children:
                if child_url in visited or any(existing[1] == child_url for existing in queue):
                    continue
                label = f"{parent_name}/{added + 1}"
                queue.append((label, child_url, level + 1))
                added += 1
                if added >= 60:
                    break
            return added > 0

        while queue and today_seen < goal and feeds_processed < max_hidden_feeds:
            state_name, feed_url, level = queue.pop(0)
            if feed_url in visited:
                continue
            visited.add(feed_url)
            feeds_processed += 1
            if live is not None:
                live.segment_name = state_name
                live.segments_done = feeds_processed - 1
                live.segments_total = max(feeds_processed, feeds_processed + len(queue))

            try:
                loc = await locate_feed(feed_url, f"hidden:{state_name}")
            except TemporaryAccessError as exc:
                unresolved = True
                log.warning("hidden date shard temporary limit category=%s state=%s http=%s", cat.name, state_name, exc.status_code)
                continue
            except Exception as exc:
                unresolved = True
                log.warning("hidden date shard failed category=%s state=%s: %s", cat.name, state_name, exc)
                continue

            status = loc["status"]
            if status in {"too_deep", "ambiguous_absent"}:
                if not add_children(state_name, loc, level):
                    unresolved = True
                continue
            if status in {"invalid", "unknown"}:
                unresolved = True
                continue
            if status == "absent":
                # This is a trustworthy zero only because locate_feed returns
                # `absent` exclusively for a fully visible feed.
                continue

            candidate = int(loc["candidate"])
            feed_limit = int(loc["limit"])
            fetch = loc["fetch"]
            page = candidate
            state_exhausted = False
            while page <= feed_limit and today_seen < goal:
                try:
                    items, relation, pairs, days = await fetch(page, "collecting")
                except TemporaryAccessError:
                    unresolved = True
                    break
                if relation in {"invalid", "unknown"}:
                    unresolved = True
                    break
                if relation == "empty":
                    state_exhausted = True
                    break
                if relation == "older" or (relation == "mixed" and not any(d == target_day for d in days)):
                    state_exhausted = True
                    break
                remaining_items = max(0, goal - today_seen)
                await process_target_items(items, pairs, limit=remaining_items)
                virtual_hidden = max(0, (today_seen - start_count + 24) // 25)
                update_live(page, days, "collecting", direct_pages_collected + virtual_hidden)
                page += 1
                if PAGE_DELAY_SECONDS:
                    await asyncio.sleep(min(PAGE_DELAY_SECONDS, 0.15))

            if page > feed_limit and not state_exhausted and today_seen < goal:
                # The target day continues beyond this feed's visible window. Drill
                # down again instead of declaring the category empty/skipped.
                if not add_children(state_name, loc, level):
                    unresolved = True

        if today_seen >= goal:
            request_complete = True
            reason = f"собрана глубина {depth} страниц выбранной даты"
            return True, unresolved

        if queue and feeds_processed >= max_hidden_feeds:
            unresolved = True

        if not unresolved and not queue:
            # All independent feeds were fully verified and the selected date ended
            # before the requested depth. This may be a real zero for a tiny category.
            request_complete = True
            reason = "выбранная дата закончилась раньше выбранной глубины"
            return False, False

        hit_limit = True
        reason = (
            f"частичный результат: выбранная дата проверена не во всех частях категории; "
            f"собрано {today_seen} объявлений выбранной даты"
        )
        return False, True

    try:
        try:
            nationwide = await locate_feed(cat.url, "nationwide")
        except TemporaryAccessError as exc:
            reason = f"временный лимит Kleinanzeigen (HTTP {exc.status_code}) во время поиска даты"
            nationwide = None

        if nationwide is not None and not reason:
            if nationwide["status"] == "found":
                outcome, direct_pages_collected = await collect_direct(nationwide)
                if outcome == "needs_hidden" and not request_complete:
                    remaining = max(0, depth - direct_pages_collected)
                    await hidden_fill(remaining)
            elif nationwide["status"] == "absent":
                request_complete = True
                reason = "выбранная дата надёжно пройдена; объявлений за неё не найдено"
            elif nationwide["status"] == "too_deep":
                await hidden_fill(depth)
            else:
                # Never convert an unknown/invalid date boundary into zero.
                await hidden_fill(depth)

        if not reason:
            reason = "завершено"

        quality_score, quality_note = _calculate_scan_quality(
            listings_parsed=listings_parsed,
            missing_dates=missing_date_count,
            missing_prices=missing_price_count,
            invalid_pages=invalid_pages,
            repeated_pages=repeated_pages,
            low_quality_pages=low_quality_pages,
            verified_pages=verified_pages,
            pages_scanned=network_requests,
            view_failures=view_failures,
            date_complete=request_complete,
        )
        update_quality_live(quality_note if quality_score < 85 else "")

        interrupted = reason.startswith("временный лимит Kleinanzeigen")
        seed_complete = bool(request_complete and not interrupted)
        seed_capped = bool(hit_limit and not interrupted)
        pages_scanned = network_requests

        await save_category_scan_state(
            cat.key,
            target_date=target_date,
            mode=mode,
            pages_scanned=pages_scanned,
            new_count=new_count,
            today_seen=today_seen,
            reason=reason,
            head_ids=first_page_head_ids,
            seed_complete=seed_complete,
            seed_capped=seed_capped,
            coverage_pages=depth if request_complete else 0,
        )

        result = ScanResult(
            new_count=new_count,
            pages_scanned=pages_scanned,
            today_seen=today_seen,
            known_count=known_total,
            enriched_count=enriched_total,
            hit_limit=hit_limit,
            reason=reason,
            mode=mode,
            avoided_pages=0,
            date_complete=request_complete,
            oldest_date_seen=oldest_date_seen,
            max_page_reached=max_page_reached,
            matched_ids=sorted(processed_target_ids),
            cards_seen=cards_seen,
            listings_parsed=listings_parsed,
            missing_date_count=missing_date_count,
            missing_price_count=missing_price_count,
            promoted_filtered=promoted_filtered,
            duplicate_count=duplicate_count,
            invalid_pages=invalid_pages,
            repeated_pages=repeated_pages,
            low_quality_pages=low_quality_pages,
            verified_pages=verified_pages,
            view_failures=view_failures,
            quality_score=quality_score,
            quality_note=quality_note,
        )
        await record_parser_run(user_id, cat, result, started_at)
        log.info(
            "category=%s v3.1-quality target=%s depth=%s requests=%s matched=%s complete=%s quality=%s "
            "cards=%s parsed=%s missing_date=%s promoted=%s duplicates=%s invalid_pages=%s repeated_pages=%s low_quality=%s views_failed=%s reason=%s",
            cat.name, target_date, depth, pages_scanned, today_seen, request_complete, quality_score,
            cards_seen, listings_parsed, missing_date_count, promoted_filtered, duplicate_count,
            invalid_pages, repeated_pages, low_quality_pages, view_failures, reason,
        )
        return result

    except Exception as exc:
        quality_score, quality_note = _calculate_scan_quality(
            listings_parsed=listings_parsed,
            missing_dates=missing_date_count,
            missing_prices=missing_price_count,
            invalid_pages=invalid_pages,
            repeated_pages=repeated_pages,
            low_quality_pages=low_quality_pages,
            verified_pages=verified_pages,
            pages_scanned=network_requests,
            view_failures=view_failures,
            date_complete=False,
        )
        failed = ScanResult(
            new_count=new_count,
            pages_scanned=network_requests,
            today_seen=today_seen,
            known_count=known_total,
            enriched_count=enriched_total,
            hit_limit=False,
            reason="ошибка",
            mode=mode,
            avoided_pages=0,
            date_complete=False,
            oldest_date_seen=oldest_date_seen,
            max_page_reached=max_page_reached,
            matched_ids=sorted(processed_target_ids),
            cards_seen=cards_seen,
            listings_parsed=listings_parsed,
            missing_date_count=missing_date_count,
            missing_price_count=missing_price_count,
            promoted_filtered=promoted_filtered,
            duplicate_count=duplicate_count,
            invalid_pages=invalid_pages,
            repeated_pages=repeated_pages,
            low_quality_pages=low_quality_pages,
            verified_pages=verified_pages,
            view_failures=view_failures,
            quality_score=quality_score,
            quality_note=quality_note,
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


async def fresh_category_cache_age(category_key: str, page_limit: int, target_date: str) -> int | None:
    """Legacy DB cache hook. Exact-depth scans use the in-memory ScanResult cache.

    Reconstructing a 25-page result from every listing ever stored for the same
    category/date would silently turn it into a 50/100-page result, so v3.0.6
    intentionally does not use the old DB-only cache for exact-date scans.
    """
    return None


async def _scan_category_task(cat, user_id: int, page_limit: int, target_date: str) -> ScanResult:
    parser = KleinanzeigenParser()
    try:
        return await scan_one_category(parser, cat, user_id, page_limit, target_date)
    finally:
        await parser.close()


async def dispatch_category(cat, user_id: int, page_limit: int, target_date: str) -> CategoryDispatchResult:
    """Reuse only results with the same category + date + requested depth."""
    inflight_key = _progress_key(cat.key, target_date, page_limit)

    if CATEGORY_CACHE_TTL_SECONDS > 0:
        cached = category_result_cache.get(inflight_key)
        if cached is not None:
            cached_at, cached_result = cached
            age = max(0, int(time.monotonic() - cached_at))
            if age <= CATEGORY_CACHE_TTL_SECONDS:
                return CategoryDispatchResult(source="cache", result=cached_result, cache_age_seconds=age)
            category_result_cache.pop(inflight_key, None)

    async with category_inflight_guard:
        task = category_inflight.get(inflight_key)
        if task is None:
            task = asyncio.create_task(
                _scan_category_task(cat, user_id, page_limit, target_date),
                name=f"category-scan:{inflight_key}",
            )
            category_inflight[inflight_key] = task
            source = "scan"
        else:
            source = "shared"

    try:
        result = await asyncio.shield(task)
        if CATEGORY_CACHE_TTL_SECONDS > 0:
            category_result_cache[inflight_key] = (time.monotonic(), result)
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
    depth = job.page_limit if job.page_limit in PAGE_LIMIT_CHOICES else 50

    if job.state == "queued":
        waited = max(0, int((datetime.utcnow() - job.created_at).total_seconds()))
        return (
            "⏳ <b>Подготавливаю парсинг…</b>\n\n"
            f"Выбрано категорий: <b>{total}</b>\n"
            f"Дата: <b>{_date_label(job.target_date)} (МСК)</b>\n"
            f"Глубина: <b>{depth} страниц от начала этой даты</b>\n"
            f"Подготовка: <b>{waited} сек</b>\n\n"
            "⚡ Сначала бот быстро найдёт стартовую страницу выбранного дня."
        )

    live = category_live_progress.get(job.current_progress_key) if job.current_progress_key else None
    current_page = live.page if live is not None else 0
    current_today = live.today_seen if live is not None else 0
    live_new = live.new_count if live is not None else 0
    live_views_ready = live.views_ready if live is not None else 0
    live_views_failed = live.views_failed if live is not None else 0

    current_fraction = 0.0
    if live is not None and live.phase == "collecting" and depth > 0:
        current_fraction = min(1.0, max(0.0, live.collection_index / depth))
    percent = int(max(0.0, min(1.0, (job.completed_categories + current_fraction) / total)) * 100)
    if job.completed_categories >= total:
        percent = 100

    elapsed = 0
    if job.started_running_monotonic:
        elapsed = max(0, int(time.monotonic() - job.started_running_monotonic))

    category_line = html.escape(job.current_category) if job.current_category else "Подготовка…"
    detail_lines = []
    if live is not None:
        if live.phase == "collecting":
            detail_lines.append("🎯 Начало выбранной даты найдено")
            if live.collection_start_page:
                detail_lines.append(f"Стартовая страница Kleinanzeigen: <b>{live.collection_start_page}</b>")
            detail_lines.append(
                f"Страниц выбранной даты: <b>{live.collection_index}/{depth}</b>"
            )
            detail_lines.append("Этап: <b>собираю выбранную глубину</b>")
        else:
            if current_page:
                detail_lines.append(f"Проверяю страницу: <b>{current_page}</b>")
            if live.current_page_date:
                hint = live.current_page_date
                if ".." in hint:
                    newer, older = hint.split("..", 1)
                    detail_lines.append(
                        f"Дата на ней: <b>{_date_label(newer)} — {_date_label(older)}</b>"
                    )
                else:
                    detail_lines.append(f"Дата на ней: <b>{_date_label(hint)}</b>")
            detail_lines.append("Этап: <b>⚡ ищу начало выбранной даты</b>")

    if current_today:
        detail_lines.append(f"Найдено объявлений: <b>{current_today}</b>")
        views = f"👁 Просмотры готовы: <b>{live_views_ready}/{current_today}</b>"
        if live_views_failed:
            views += f" · ошибок: <b>{live_views_failed}</b>"
        detail_lines.append(views)
    if live is not None and live.date_coverage_pct:
        quality_icon = "🟢" if live.quality_score >= 90 else "🟡" if live.quality_score >= 75 else "🔴"
        detail_lines.append(
            f"{quality_icon} Проверка качества: <b>{live.quality_score}/100</b> · дат распознано <b>{live.date_coverage_pct}%</b>"
        )

    visible_new = job.total_new + live_new
    detail_text = "\n".join(detail_lines)
    if detail_text:
        detail_text = "\n" + detail_text

    return (
        "🔄 <b>Парсинг идёт</b>\n\n"
        f"{_progress_bar(percent)} <b>{percent}%</b>\n"
        f"Категории: <b>{job.completed_categories}/{total}</b> готово\n"
        f"Дата: <b>{_date_label(job.target_date)} (МСК)</b>\n"
        f"Глубина: <b>{depth} страниц от начала даты</b>\n"
        f"Сейчас: <b>{category_line}</b> · категория <b>{max(1, job.current_category_index)}/{total}</b>{detail_text}\n\n"
        f"🆕 Найдено новых: <b>{visible_new}</b>\n"
        f"⏱ Прошло: <b>{_human_duration(elapsed)}</b>\n\n"
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

    job.state = "cancelled" if cancelled else ("partial" if job.incomplete_categories else "done")
    if cancelled:
        text = (
            "❌ <b>Парсинг отменён</b>\n\n"
            f"Категорий обработано: <b>{job.completed_categories}/{len(job.category_keys)}</b>\n"
            f"Новых найдено: <b>{job.total_new}</b>\n"
            f"⏱ Время: <b>{elapsed_text}</b>"
        )
    else:
        headline = "⚠️ <b>Парсинг завершён частично</b>" if job.incomplete_categories else "✅ <b>Парсинг завершён</b>"
        completeness_line = (
            f"⚠️ Не удалось надёжно начать/закончить выбранную глубину в категориях: <b>{job.incomplete_categories}</b>\n"
            if job.incomplete_categories else ""
        )
        quality_values = [int(x) for x in (job.quality_scores or []) if x is not None]
        quality_avg = round(sum(quality_values) / len(quality_values)) if quality_values else 0
        text = (
            f"{headline}\n\n"
            f"🗂 Категорий обработано: <b>{job.completed_categories}/{len(job.category_keys)}</b>\n"
            f"🛡 Качество данных: <b>{quality_avg}/100</b>\n"
            f"📅 Дата объявлений: <b>{_date_label(job.target_date)} (МСК)</b>\n"
            f"📄 Глубина: <b>{job.page_limit} страниц от начала даты</b>\n"
            f"{completeness_line}"
            f"🆕 Новых объявлений: <b>{job.total_new}</b>\n"
            f"⏱ Время: <b>{elapsed_text}</b>\n\n"
            "📄 Формирую файл с результатом…"
        )
    await edit_job_status(bot, job, text, force=True)

    if not cancelled:
        try:
            settings = await get_settings(job.user_id)
            result_prefix = (
                ("⚠️ <b>Частичный результат</b>\n" if job.incomplete_categories else "✅ <b>Готовый результат</b>\n")
                +
                f"🗂 Категорий: <b>{job.completed_categories}/{len(job.category_keys)}</b>\n"
                f"📅 Дата: <b>{_date_label(job.target_date)} (МСК)</b>\n"
                f"📄 Глубина: <b>{job.page_limit} страниц от начала даты</b>\n"
                f"⚡ Поиск даты: <b>быстрый переход к старту</b>\n"
                f"🆕 Новых найдено: <b>{job.total_new}</b>\n"
                f"🛡 Качество: <b>{round(sum(job.quality_scores or [0]) / max(1, len(job.quality_scores or [])))}/100</b>\n"
                f"⏱ Время: <b>{elapsed_text}</b>\n"
                f"Режим: <b>{MODE_LABELS.get(settings.output_mode, settings.output_mode)}</b>\n"
                f"Дата поиска: <b>{_date_label(job.target_date)} (МСК)</b>\n"
                f"Цена: <b>{PRICE_LABELS.get(settings.price_filter, settings.price_filter)}</b>"
            )
            adapter = BotChatAdapter(
                bot,
                job.chat_id,
                prefix=result_prefix,
                reply_markup=post_scan_keyboard(job.scan_id),
            )
            snapshot_rows = []
            if job.scan_id is not None:
                snapshot_rows = [row for row, _ in await get_scan_rows(job.scan_id)]
            await send_smart_export(
                adapter,
                job.user_id,
                len(job.category_keys),
                category_keys_override=set(job.category_keys),
                rows_override=snapshot_rows,
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
    job.scan_notes = job.scan_notes or []
    job.matched_ids = job.matched_ids or set()
    job.quality_scores = job.quality_scores or []
    job.quality_notes = job.quality_notes or []
    await edit_job_status(bot, job, render_user_job_status(job), force=True)

    for idx, key in enumerate(job.category_keys, start=1):
        if job.cancel_requested:
            break
        cat = CATEGORIES.get(key)
        if cat is None:
            job.warnings.append(f"Неизвестная категория: {key}")
            job.incomplete_categories += 1
            job.completed_categories += 1
            job.quality_scores = job.quality_scores or []
            job.quality_scores.append(0)
            continue
        job.current_category = cat.name
        job.current_category_key = cat.key
        job.current_progress_key = _progress_key(cat.key, job.target_date, job.page_limit)
        job.current_category_index = idx
        # Multi-category isolation: every selected category starts with a clean
        # live-progress slot and performs its own date location cycle. No boundary
        # or progress state from the previous category may leak into this one.
        category_live_progress.pop(job.current_progress_key, None)
        log.info(
            "multi-category start job=%s category=%s index=%s/%s target=%s depth=%s",
            job.job_id, cat.name, idx, len(job.category_keys), job.target_date, job.page_limit,
        )
        try:
            await edit_job_status(bot, job, render_user_job_status(job), force=True)
            dispatched = await dispatch_category(cat, job.user_id, job.page_limit, job.target_date)
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
                job.matched_ids = job.matched_ids or set()
                job.matched_ids.update(result.matched_ids or [])
                job.quality_scores = job.quality_scores or []
                job.quality_notes = job.quality_notes or []
                job.quality_scores.append(int(result.quality_score or 0))
                job.quality_notes.append(f"{cat.name}: {result.quality_score}/100 — {result.quality_note}")
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
                if not result.date_complete:
                    job.incomplete_categories += 1
                    reached = _date_label(result.oldest_date_seen) if result.oldest_date_seen else "не определена"
                    note = (
                        f"{cat.name}: охват {_date_label(job.target_date)} не подтверждён полностью; "
                        f"самая старая распознанная дата — {reached}; "
                        f"сетевых запросов {result.pages_scanned}; качество {result.quality_score}/100; "
                        f"причина: {result.reason}"
                    )
                    job.warnings.append(note)
                    job.scan_notes = job.scan_notes or []
                    job.scan_notes.append(note)
                elif result.reason.startswith("временный лимит Kleinanzeigen"):
                    job.warnings.append(
                        f"{cat.name}: Kleinanzeigen временно ограничил запросы; "
                        f"успели сделать {result.pages_scanned} запросов (до стр. {result.max_page_reached or '?'}), можно повторить позже"
                    )

            log.info(
                "multi-category finish job=%s category=%s matched=%s complete=%s reason=%s",
                job.job_id, cat.name, (result.today_seen if result is not None else 0),
                (result.date_complete if result is not None else False),
                (result.reason if result is not None else "no result"),
            )
            job.completed_categories += 1

            # If an interactive category already spent the full recovery window and
            # Kleinanzeigen still refuses the process, immediately trying the next
            # selected category only extends the block. Stop this job gracefully;
            # completed categories remain saved and no false zeros are produced.
            if result is not None and "временный лимит Kleinanzeigen" in (result.reason or ""):
                remaining_categories = max(0, len(job.category_keys) - idx)
                if remaining_categories:
                    note = (
                        f"Kleinanzeigen всё ещё ограничивает доступ после автоматического ожидания. "
                        f"Оставшиеся категории ({remaining_categories}) не запускались, чтобы не усиливать лимит."
                    )
                    job.warnings.append(note)
                    job.scan_notes = job.scan_notes or []
                    job.scan_notes.append(note)
                    job.incomplete_categories += remaining_categories
                    job.completed_categories += remaining_categories
                break
            # User sees only useful progress; cache/shared/worker details stay internal.
            await edit_job_status(bot, job, render_user_job_status(job), force=True)
        except Exception as exc:
            log.exception("Queue scan error job=%s category=%s", job.job_id, cat.name)
            note = f"{cat.name}: ошибка скана — {str(exc)[:160]}"
            job.warnings.append(note)
            job.scan_notes = job.scan_notes or []
            job.scan_notes.append(note)
            job.quality_scores = job.quality_scores or []
            job.quality_notes = job.quality_notes or []
            job.quality_scores.append(0)
            job.quality_notes.append(f"{cat.name}: 0/100 — ошибка скана")
            job.incomplete_categories += 1
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
                await TRAFFIC.scan_job_started()
                try:
                    await process_scan_job(bot, job, worker_id)
                finally:
                    await TRAFFIC.scan_job_finished()
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



async def enqueue_user_scan(message: Message, user_id: int, category_keys: list[str], page_limit: int, target_date: str) -> ScanJob:
    """Create a persistent scan card and queue the network job."""
    job_uid = uuid.uuid4().hex[:12]
    scan = await create_user_scan(user_id, job_uid, category_keys, page_limit, target_date)
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
        target_date=target_date,
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
        "<b>🔍 Kleinanzeigen Parser v3.1.3</b>\n\n"
        "Здесь всё строится вокруг сохранённых сканов:\n"
        "🔥 <b>Популярное сейчас</b> — лидеры по просмотрам\n"
        "🔎 <b>Новый скан</b> — собрать свежие объявления\n"
        "📊 <b>Мои сканы</b> — вернуться к любому запуску, обновить просмотры и увидеть рост\n"
        "🧠 <b>Распознавание</b> — бот объединяет разные написания одной модели и сохраняет важные версии/память\n\n"
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
    await callback.message.edit_text("<b>🔍 Kleinanzeigen Parser v3.1.3</b>\n\nЧто хочешь посмотреть?", reply_markup=main_keyboard(len(selected)), parse_mode=ParseMode.HTML)


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
        "<b>🔍 Kleinanzeigen Parser v3.1.3</b>\n\nЧто хочешь посмотреть?",
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
            "✅ Обычный массовый парсинг v3.1.3 уже сначала использует быстрый способ. Chromium включается только для объявлений, где прямой счётчик не сработал.",
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
    items = await get_user_popular_categories(callback.from_user.id)
    await callback.answer()
    if not items:
        text = (
            "🔥 <b>Популярное сейчас</b>\n\n"
            "Здесь появятся только категории, которые ты уже сканировал. "
            "Сначала сделай хотя бы один скан с просмотрами."
        )
    else:
        text = (
            "🔥 <b>Популярное сейчас</b>\n\n"
            "Выбери категорию. Рейтинги разных категорий больше не смешиваются.\n\n"
            "🚀 TOP 1/3/6/12/24ч — по <b>реальному приросту просмотров</b>.\n"
            "👁 Самые просматриваемые — отдельный рейтинг по общему числу просмотров."
        )
    try:
        await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=popular_categories_keyboard(items),
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode=ParseMode.HTML,
            reply_markup=popular_categories_keyboard(items),
            disable_web_page_preview=True,
        )


@dp.callback_query(F.data.startswith("popularcat:"))
async def popular_category(callback: CallbackQuery) -> None:
    category_key = callback.data.split(":", 1)[1]
    cat = CATEGORIES.get(category_key)
    scan = await get_latest_scan_for_category(callback.from_user.id, category_key)
    if cat is None or scan is None:
        await callback.answer("Категория или скан не найдены", show_alert=True); return
    pairs = await get_scan_rows(scan.id)
    rows = [row for row, _ in pairs if row.category_key == category_key]
    viewed = sum(1 for row in rows if row.view_count is not None)
    await callback.answer()
    text = (
        f"🔥 <b>Популярное · {html.escape(cat.name)}</b>\n\n"
        f"Используется последний скан этой категории: <b>{_date_label(scan.target_date)}</b>\n"
        f"📦 Объявлений: <b>{len(rows)}</b>\n"
        f"👁 С просмотрами: <b>{viewed}</b>\n"
        f"🕐 Первый замер: <b>{_moscow_text(scan.finished_at or scan.created_at)} МСК</b>\n\n"
        "Выбери рейтинг. Для TOP роста бот сравнивает контрольные замеры с первым замером этого скана."
    )
    await callback.message.answer(
        text, parse_mode=ParseMode.HTML,
        reply_markup=popular_category_keyboard(scan.id, category_key),
    )


@dp.callback_query(F.data.startswith("pcv:"))
async def popular_category_views(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректный запрос", show_alert=True); return
    scan_id, category_key = int(parts[1]), parts[2]
    scan = await get_user_scan(callback.from_user.id, scan_id)
    cat = CATEGORIES.get(category_key)
    if scan is None or cat is None or category_key not in _scan_category_keys(scan):
        await callback.answer("Скан не найден", show_alert=True); return
    pairs = await get_scan_rows(scan_id)
    rows = [row for row, _ in pairs if row.category_key == category_key and row.view_count is not None]
    rows.sort(key=lambda row: (row.view_count or 0, row.first_seen_at), reverse=True)
    await callback.answer()
    if not rows:
        text = f"👁 <b>{html.escape(cat.name)}</b>\n\nПока нет данных просмотров."
    else:
        lines = [f"👁 <b>Самые просматриваемые · {html.escape(cat.name)}</b>", ""]
        for i, row in enumerate(rows[:GROWTH_TELEGRAM_LIMIT], 1):
            model = f"\n🧠 {html.escape(row.identity_label[:75])}" if row.identity_label and (row.identity_confidence or 0) >= 70 else ""
            lines.append(
                f"<b>{i}. {html.escape(row.title[:60])}</b>{model}\n"
                f"👁 <b>{row.view_count}</b> · 💶 {html.escape(_price_display(row.price_text, row.price_eur))}\n"
                f'<a href="{html.escape(row.url)}">Открыть</a>'
            )
        text = "\n\n".join(lines)
    await callback.message.answer(
        text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=popular_category_keyboard(scan_id, category_key),
    )


@dp.callback_query(F.data.startswith("pcg:"))
async def popular_category_growth(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Некорректный запрос", show_alert=True); return
    try:
        scan_id, category_key, period_hours = int(parts[1]), parts[2], int(parts[3])
    except Exception:
        await callback.answer("Некорректный запрос", show_alert=True); return
    if period_hours not in OBSERVATION_HOURS:
        period_hours = 1
    scan = await get_user_scan(callback.from_user.id, scan_id)
    cat = CATEGORIES.get(category_key)
    if scan is None or cat is None or category_key not in _scan_category_keys(scan):
        await callback.answer("Скан не найден", show_alert=True); return
    growth, rounds = await get_scan_growth_rows(scan_id, period_hours, category_key=category_key)
    await callback.answer()
    period_label = f"{period_hours} ч"
    if not growth:
        text = (
            f"🚀 <b>TOP роста · {html.escape(cat.name)} · {period_label}</b>\n\n"
            "Контрольный замер для этого периода ещё не готов или прироста пока нет. "
            "Автоматические замеры выполняются через 1 / 3 / 6 / 12 / 24 часа после первого скана."
        )
    else:
        lines = [
            f"🚀 <b>TOP роста · {html.escape(cat.name)} · {period_label}</b>",
            "Сортировка: <b>кто набрал больше всего новых просмотров</b>.",
            "",
        ]
        for i, item in enumerate(growth[:GROWTH_TELEGRAM_LIMIT], 1):
            row = item.listing
            model = f"\n🧠 {html.escape(row.identity_label[:75])}" if row.identity_label and (row.identity_confidence or 0) >= 70 else ""
            lines.append(
                f"<b>{i}. {html.escape(row.title[:60])}</b>{model}\n"
                f"👁 {item.base_views} → <b>{item.current_views}</b> · "
                f"🚀 <b>+{item.delta}</b> · ⚡ {item.per_hour:.1f}/ч\n"
                f"💶 {html.escape(_price_display(row.price_text, row.price_eur))} · "
                f'<a href="{html.escape(row.url)}">Открыть</a>'
            )
        lines += ["", f"📊 Полный рейтинг: до <b>{GROWTH_TOP_LIMIT}</b> товаров в таблице."]
        text = "\n\n".join(lines)
    await callback.message.answer(
        text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=growth_period_keyboard(scan_id, period_hours, category_key=category_key),
    )


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
    recognized = [row for row in rows if (row.identity_confidence or 0) >= 70 and row.identity_key]
    recognized_models = len({row.identity_key for row in recognized})
    recognition_pct = round(len(recognized) / len(rows) * 100) if rows else 0
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
    history_rounds = await get_scan_history_rounds(scan.id, limit=50)
    observation_statuses = await get_scan_observation_statuses(scan.id)
    status_icons = {"done": "✅", "pending": "⏳", "running": "🔄", "missed": "▫️", "error": "⚠️"}
    observation_line = " · ".join(
        f"{hours}ч {status_icons.get(observation_statuses.get(hours, 'pending'), '⏳')}"
        for hours in OBSERVATION_HOURS
    )
    quality_value = int(getattr(scan, "quality_score", 0) or 0)
    quality_label = f"{quality_value}/100" if quality_value > 0 else "нет замера (старый скан)"
    status_label = {
        "done": "✅ завершён",
        "partial": "⚠️ частичный",
        "running": "🔄 идёт",
        "queued": "⏳ ожидает",
        "cancelled": "❌ отменён",
        "failed": "❌ ошибка",
    }.get(scan.status, scan.status)
    lines = [
        f"<b>📊 {html.escape(scan.title)}</b>",
        "",
        f"Статус: <b>{status_label}</b>",
        f"Запуск: <b>{_moscow_text(scan.created_at)} МСК</b>",
        f"Дата объявлений: <b>{_date_label(scan.target_date)} (МСК)</b>",
        f"Глубина: <b>{scan.page_limit if scan.page_limit in PAGE_LIMIT_CHOICES else 50} страниц от начала даты</b>",
        "Поиск даты: <b>⚡ быстрый переход к стартовой странице</b>",
        f"Категорий: <b>{scan.completed_categories}/{scan.total_categories}</b>",
        f"🛡 Качество парсинга: <b>{quality_label}</b>",
        "",
        f"📦 В снимке: <b>{len(rows)}</b> объявлений",
        f"👁 С просмотрами: <b>{viewed}</b>",
        f"🧠 Распознано уверенно: <b>{len(recognized)}</b> ({recognition_pct}%) · моделей: <b>{recognized_models}</b>",
        f"🚀 Выросли после скана: <b>{growers}</b>" + (f" · суммарно +{total_growth}" if total_growth else ""),
        f"🆕 Новых после скана, уже найденных последующими сканами: <b>{new_since}</b>",
        f"❌ Исчезли: <b>{disappeared}</b>",
        f"📈 Точек наблюдения: <b>{len(history_rounds)}</b>",
        f"🔔 Автозамеры: <b>{observation_line}</b>",
    ]
    if scan.last_view_refresh_at:
        lines += ["", f"Последнее обновление просмотров: <b>{_moscow_text(scan.last_view_refresh_at)} МСК</b>"]
    if getattr(scan, "quality_note", ""):
        lines += ["", f"🩺 <b>Проверка:</b> {html.escape(scan.quality_note)}"]
    if scan.status == "partial" and getattr(scan, "scan_note", ""):
        lines += ["", f"⚠️ <b>Почему результат частичный:</b> {html.escape(scan.scan_note)}"]
    lines += ["", "💡 Автозамеры идут через 1 / 3 / 6 / 12 / 24 часа. Открывай «🚀 TOP роста» — там TOP-10 в боте и TOP-50 таблицей."]
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


@dp.callback_query(F.data.startswith("scanproducts:"))
async def scan_products(callback: CallbackQuery) -> None:
    try:
        scan_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Скан не найден", show_alert=True); return
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return

    pairs = await get_scan_rows(scan_id)
    all_rows = [row for row, _ in pairs]
    recognized = [
        row for row in all_rows
        if row.identity_key and row.identity_label and (row.identity_confidence or 0) >= 70
    ]
    groups: dict[str, list[Listing]] = {}
    for row in recognized:
        groups.setdefault(row.identity_key, []).append(row)

    ranked = sorted(
        groups.values(),
        key=lambda items: (
            len(items),
            max((row.view_count or 0) for row in items),
            max((row.first_seen_at for row in items), default=datetime.min),
        ),
        reverse=True,
    )
    await callback.answer()
    if not all_rows:
        text = "🧠 <b>Распознанные модели</b>\n\nВ этом скане пока нет объявлений."
    elif not ranked:
        text = (
            "🧠 <b>Распознанные модели</b>\n\n"
            "Пока нет моделей с уверенностью распознавания от 70%. "
            "Такие объявления остаются в результате, но не смешиваются в ценовые группы."
        )
    else:
        coverage = round(len(recognized) / len(all_rows) * 100)
        lines = [
            f"🧠 <b>Модели скана: {html.escape(scan.title)}</b>",
            f"Распознано уверенно: <b>{len(recognized)}/{len(all_rows)} ({coverage}%)</b> · групп: <b>{len(ranked)}</b>",
            "",
        ]
        for i, items in enumerate(ranked[:15], 1):
            example = max(items, key=lambda row: ((row.view_count or 0), row.first_seen_at))
            prices = [row.price_eur for row in items if row.price_eur is not None and row.price_eur > 0]
            median_price = int(statistics.median(prices)) if prices else None
            max_views = max((row.view_count or 0) for row in items)
            type_label = TYPE_DISPLAY.get(example.identity_product_type or "", example.identity_product_type or "товар")
            price_part = f" · 💶 медиана {median_price} €" if median_price is not None else ""
            views_part = f" · 👁 макс. {max_views}" if max_views else ""
            lines.append(
                f"<b>{i}. {html.escape(example.identity_label or example.title)}</b>\n"
                f"{html.escape(type_label)} · 📦 {len(items)} объявл.{price_part}{views_part}\n"
                f"Точность: {example.identity_confidence or 0}% · "
                f'<a href="{html.escape(example.url)}">пример</a>'
            )
        lines += [
            "",
            "💡 Разные написания одной модели объединяются, а важные версии, память и RAM сохраняются отдельно.",
        ]
        text = "\n\n".join(lines)
    await callback.message.answer(
        text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=scan_detail_keyboard(scan_id),
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
            identity_line = ""
            if row.identity_label and (row.identity_confidence or 0) >= 70:
                identity_line = f"🧠 {html.escape(row.identity_label[:75])}\n"
            lines.append(
                f"<b>{i}. {html.escape(row.title[:55])}</b>\n"
                f"{identity_line}"
                f"👁 {row.view_count}{growth} · 💶 {html.escape(_price_display(row.price_text, row.price_eur))}\n"
                f"<a href=\"{html.escape(row.url)}\">Открыть</a>"
            )
        text = "\n\n".join(lines)
    await callback.message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=scan_detail_keyboard(scan_id))


@dp.callback_query(F.data.startswith("scangrowth:"))
async def scan_growth(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    try:
        scan_id = int(parts[1])
        period_hours = int(parts[2]) if len(parts) > 2 else 1
    except Exception:
        await callback.answer("Скан не найден", show_alert=True); return
    if period_hours not in OBSERVATION_HOURS:
        period_hours = 1
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return

    growth, rounds = await get_scan_growth_rows(scan_id, period_hours)
    await callback.answer()
    period_label = f"{period_hours} ч"
    if not growth:
        text = (
            f"🚀 <b>TOP роста за {period_label}</b>\n\n"
            "Контрольный замер для этого периода ещё не готов или прироста пока нет. "
            "Бот автоматически делает замеры через 1 / 3 / 6 / 12 / 24 часа после первого скана."
        )
    else:
        lines = [
            f"🚀 <b>TOP роста · {period_label}</b>",
            f"<b>{html.escape(scan.title)}</b>",
            "Сортировка: <b>по реальному приросту просмотров</b>.",
            "",
        ]
        for i, item in enumerate(growth[:GROWTH_TELEGRAM_LIMIT], 1):
            row = item.listing
            identity_line = ""
            if row.identity_label and (row.identity_confidence or 0) >= 70:
                identity_line = f"🧠 {html.escape(row.identity_label[:75])}\n"
            lines.append(
                f"<b>{i}. {html.escape(row.title[:60])}</b>\n"
                f"{identity_line}"
                f"👁 {item.base_views} → <b>{item.current_views}</b> · "
                f"🚀 <b>+{item.delta}</b> · ⚡ {item.per_hour:.1f}/ч\n"
                f"💶 {html.escape(_price_display(row.price_text, row.price_eur))} · "
                f'<a href="{html.escape(row.url)}">Открыть</a>'
            )
        lines += ["", f"📊 Полный рейтинг: до <b>{GROWTH_TOP_LIMIT}</b> товаров в таблице."]
        text = "\n\n".join(lines)
    await callback.message.answer(
        text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=growth_period_keyboard(scan_id, period_hours),
    )


def build_growth_top_xlsx(
    scan: UserScan, period_hours: int, growth: list[GrowthMetric], category_key: str | None = None
) -> Path:
    """Build the downloadable TOP-50 table."""
    cat = CATEGORIES.get(category_key) if category_key else None
    wb = Workbook()
    ws = wb.active
    ws.title = "TOP growth"
    title = f"TOP-{min(GROWTH_TOP_LIMIT, len(growth))} роста за {period_hours}ч"
    if cat is not None:
        title += f" · {cat.name}"
    ws.append([title])
    ws.merge_cells("A1:M1")
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append([
        "#", "Товар", "Распознанная модель", "Категория", "Цена €",
        "Было просмотров", "Сейчас просмотров", "Прирост", "Просмотров/час",
        "Фактический интервал, ч", "Дата объявления", "ID", "Ссылка",
    ])
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[2]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for idx, item in enumerate(growth[:GROWTH_TOP_LIMIT], 1):
        row = item.listing
        ws.append([
            idx, row.title, row.identity_label or "", row.category, row.price_eur,
            item.base_views, item.current_views, item.delta, round(item.per_hour, 2),
            round(item.elapsed_hours, 2), row.posted_date_msk or row.posted_text or "",
            row.external_id, row.url,
        ])
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:M{max(2, ws.max_row)}"
    widths = {
        "A": 6, "B": 44, "C": 34, "D": 26, "E": 11, "F": 16, "G": 17,
        "H": 12, "I": 16, "J": 19, "K": 16, "L": 16, "M": 52,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    for row_cells in ws.iter_rows(min_row=3):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    growth_fill = PatternFill("solid", fgColor="E2F0D9")
    for cell in ws["H"][2:]:
        cell.fill = growth_fill
        cell.font = Font(bold=True)
    for cell in ws["M"][2:]:
        if cell.value:
            cell.hyperlink = str(cell.value)
            cell.style = "Hyperlink"

    out_dir = Path(tempfile.mkdtemp(prefix="growth_top_"))
    safe_cat = re.sub(r"[^A-Za-z0-9_-]+", "_", category_key or "scan")
    out = out_dir / f"TOP50_{safe_cat}_{period_hours}h_scan_{scan.id}.xlsx"
    wb.save(out)
    return out


async def send_growth_xlsx(
    message: Message, scan: UserScan, period_hours: int, category_key: str | None = None
) -> None:
    growth, _ = await get_scan_growth_rows(scan.id, period_hours, category_key=category_key)
    if not growth:
        await message.answer(
            f"📊 TOP-{GROWTH_TOP_LIMIT} за {period_hours}ч пока нельзя сформировать: "
            "контрольный замер ещё не готов или прироста нет."
        )
        return
    path = build_growth_top_xlsx(scan, period_hours, growth, category_key=category_key)
    try:
        cat = CATEGORIES.get(category_key) if category_key else None
        suffix = f" · {cat.name}" if cat else ""
        await message.answer_document(
            FSInputFile(path),
            caption=f"📊 TOP-{min(GROWTH_TOP_LIMIT, len(growth))} роста за {period_hours}ч{suffix}",
        )
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


@dp.callback_query(F.data.startswith("scangrowthexport:"))
async def scan_growth_export(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    try:
        scan_id, period_hours = int(parts[1]), int(parts[2])
    except Exception:
        await callback.answer("Некорректный запрос", show_alert=True); return
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None or period_hours not in OBSERVATION_HOURS:
        await callback.answer("Скан не найден", show_alert=True); return
    await callback.answer("Формирую TOP-50")
    await send_growth_xlsx(callback.message, scan, period_hours)


@dp.callback_query(F.data.startswith("pce:"))
async def popular_growth_export(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Некорректный запрос", show_alert=True); return
    try:
        scan_id, category_key, period_hours = int(parts[1]), parts[2], int(parts[3])
    except Exception:
        await callback.answer("Некорректный запрос", show_alert=True); return
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if (
        scan is None or period_hours not in OBSERVATION_HOURS
        or category_key not in _scan_category_keys(scan)
    ):
        await callback.answer("Скан не найден", show_alert=True); return
    await callback.answer("Формирую TOP-50")
    await send_growth_xlsx(callback.message, scan, period_hours, category_key=category_key)


@dp.callback_query(F.data.startswith("scanhistory:"))
async def scan_history(callback: CallbackQuery) -> None:
    try:
        scan_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Скан не найден", show_alert=True); return
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    rounds = await get_scan_history_rounds(scan_id, limit=10)
    await callback.answer()
    if not rounds:
        text = "🕘 <b>История просмотров</b>\n\nПока нет сохранённых точек наблюдения."
    else:
        lines = [f"🕘 <b>История: {html.escape(scan.title)}</b>", "", "Последние точки по московскому времени:"]
        for idx, (recorded_at, count, total_views) in enumerate(rounds, 1):
            marker = "🟢" if idx == 1 else "▫️"
            lines.append(
                f"{marker} <b>{_moscow_text(recorded_at)} МСК</b> · "
                f"{count} объявл. · суммарно 👁 {total_views}"
            )
        lines += ["", "Каждое ручное «Обновить просмотры» добавляет новую точку для сравнения."]
        text = "\n".join(lines)
    await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=scan_detail_keyboard(scan_id))


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
    await refresh_view_counts(rows, callback.message, force=True)
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
    repeat_depth = scan.page_limit if scan.page_limit in PAGE_LIMIT_CHOICES else 50
    await callback.answer("Повторяю скан")
    await enqueue_user_scan(callback.message, callback.from_user.id, keys, repeat_depth, scan.target_date or _moscow_today_iso())


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
async def start_scan(callback: CallbackQuery, state: FSMContext) -> None:
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

    await state.set_state(ScanInput.target_date)
    await callback.answer()
    today_label = datetime.now(MOSCOW).strftime("%d.%m.%Y")
    await callback.message.answer(
        "<b>📅 За какое число искать объявления?</b>\n\n"
        "Всё считаем по <b>московскому времени (МСК)</b>.\n"
        f"Сегодня по МСК: <b>{today_label}</b>.\n\n"
        "Можно отправить просто число текущего месяца, например <code>12</code>, "
        "или полную дату <code>10.08.2026</code>.\n\n"
        "После даты выбери глубину <b>25 / 50 / 100 страниц</b>. Бот сначала быстро "
        "найдёт начало нужного дня, а выбранная глубина будет считаться уже от этой точки.",
        parse_mode=ParseMode.HTML,
    )


@dp.message(ScanInput.target_date)
async def receive_scan_date(message: Message, state: FSMContext) -> None:
    if not allowed(message.from_user.id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    target_date = _parse_scan_date_input(message.text)
    if target_date is None:
        await message.answer(
            "⚠️ Не понял дату. Отправь, например, <code>12</code> или <code>10.08.2026</code>. "
            "Будущую дату выбрать нельзя.",
            parse_mode=ParseMode.HTML,
        )
        return
    await state.update_data(target_date=target_date)
    selected = await get_selected(message.from_user.id)
    selected_cats = [CATEGORIES[k] for k in CATEGORIES if k in selected]
    if not selected_cats:
        await state.clear()
        await message.answer("Сначала выбери хотя бы одну категорию.")
        return

    await message.answer(
        f"<b>📅 Дата: {_date_label(target_date)} (МСК)</b>\n\n"
        "Теперь выбери глубину. Эти страницы будут считаться <b>от начала выбранной даты</b>, "
        "а не от первой страницы Kleinanzeigen.\n\n"
        "Например, если 10.08 начинается примерно на странице 1700, при выборе 50 бот "
        "быстро найдёт эту точку и соберёт максимум 50 страниц начиная с неё.",
        parse_mode=ParseMode.HTML,
        reply_markup=page_limit_keyboard(),
    )



@dp.callback_query(F.data.startswith("scanpages:"))
async def start_scan_with_pages(callback: CallbackQuery, state: FSMContext) -> None:
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

    data = await state.get_data()
    target_date = data.get("target_date") or _moscow_today_iso()
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
    await state.clear()
    await callback.answer("Запускаю скан")
    await callback.message.answer(
        f"<b>🔎 Запускаю скан</b>\n"
        f"Дата: <b>{_date_label(target_date)} (МСК)</b>\n"
        f"Глубина: <b>{page_limit} страниц от начала этой даты</b>\n"
        "⚡ Сначала быстро найду стартовую страницу, затем начнётся обычный сбор.",
        parse_mode=ParseMode.HTML,
    )
    await enqueue_user_scan(
        callback.message, callback.from_user.id, [cat.key for cat in selected_cats], page_limit, target_date
    )


async def main() -> None:
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not set")
    await init_db()
    backfilled = await backfill_product_identities()
    if backfilled:
        log.info("v3.0 product identity backfill: %s listings", backfilled)
    recovered = await recover_running_observations()
    planned = await backfill_recent_observation_plans()
    if recovered or planned:
        log.info("v3.1 observations: recovered=%s recent_scans_planned=%s", recovered, planned)
    bot = Bot(BOT_TOKEN)
    me = await bot.get_me()
    traffic = await TRAFFIC.snapshot()
    log.info(
        "Starting @%s | workers=%s cache_ttl=%ss | traffic scan=%s view=%s browser=%s global=%s",
        me.username, MAX_CONCURRENT_JOBS, CATEGORY_CACHE_TTL_SECONDS,
        traffic.scan_limit, traffic.view_limit, traffic.browser_limit, traffic.global_limit,
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
    observation_tasks = [
        asyncio.create_task(observation_scheduler(bot, i), name=f"view-observation-worker-{i}")
        for i in range(1, OBSERVATION_CONCURRENCY + 1)
    ]
    try:
        await dp.start_polling(bot)
    finally:
        ticker_task.cancel()
        for task in observation_tasks:
            task.cancel()
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(ticker_task, *observation_tasks, *worker_tasks, return_exceptions=True)
        async with category_inflight_guard:
            inflight = list(category_inflight.values())
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
