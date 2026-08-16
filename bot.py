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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand, BotCommandScopeChat, BotCommandScopeDefault, CallbackQuery, FSInputFile,
    InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Message,
)
from sqlalchemy import delete, func, select, update
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from categories import CATEGORIES, GROUPS, categories_for_group, group_root_key
from db import DATABASE_BACKEND, SessionLocal, init_db
from filters import (
    apply_listing_settings,
    base_filter,
    below_market_rows,
    dedupe_rows,
    disappearing_rows,
    frequent_rows,
    price_drop_rows,
    sort_rows,
    unique_rows,
)
from models import BotUser, CategoryScanState, Listing, ParserRun, PriceHistory, ScanListing, ScanObservation, ScanViewHistory, SelectedCategory, SubscriptionPayment, SubscriptionPlan, UserScan, UserSettings, ViewHistory
from product_identity import TYPE_DISPLAY, ProductIdentity, recognize_product
from traffic import TRAFFIC
from scan_control import ScanStopRequested, wait_for_task_or_stop
from scan_selection import MAX_SELECTED_CATEGORIES, bulk_group_selection, toggle_selection, validate_scan_category_keys
from commerce import (
    PAYMENT_POLL_SECONDS, PaymentProviderError, admin_stats, cached_access_until,
    create_subscription_payment, current_access_mode, find_users, get_payment,
    get_plan, get_plans, get_user as get_commerce_user, grant_access_days,
    has_access, initialize_commerce, is_banned_cached, pending_payments,
    provider_enabled, providers_status, recent_payments, recent_users,
    refresh_payment, revoke_access, set_access_mode, set_banned, toggle_plan,
    touch_user, update_plan_price, set_onboarding_completed, user_payments,
    subscription_notice_candidates, mark_subscription_notice,
)
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
    private_provider_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("kleinanzeigen-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
APP_VERSION = "3.4.3"
MENU_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "dt_parser_menu.png"
BERLIN = ZoneInfo("Europe/Berlin")
MOSCOW = ZoneInfo("Europe/Moscow")
AVAILABILITY_CHECK_LIMIT = max(1, int(os.getenv("AVAILABILITY_CHECK_LIMIT", "150")))
AVAILABILITY_CONCURRENCY = max(1, min(8, int(os.getenv("AVAILABILITY_CONCURRENCY", "4"))))

# v2.6 Multi-User Core. User launches go into a queue. Only a limited number
# of jobs are processed at once, while category scans are shared globally.
MAX_CONCURRENT_JOBS = max(1, min(12, int(os.getenv("MAX_CONCURRENT_JOBS", "5"))))
MAX_QUEUE_SIZE = max(10, int(os.getenv("MAX_QUEUE_SIZE", "200")))
CATEGORY_CACHE_TTL_SECONDS = max(0, int(os.getenv("CATEGORY_CACHE_TTL_SECONDS", "300")))
STATUS_UPDATE_INTERVAL_SECONDS = max(0.5, float(os.getenv("STATUS_UPDATE_INTERVAL_SECONDS", "1.5")))
# v3.3.0 job-level resilience on top of parser HTTP retries. A category that
# throws an unexpected transient exception gets one controlled retry before the
# user receives a partial result.
SCAN_CATEGORY_ATTEMPTS = max(1, min(3, int(os.getenv("SCAN_CATEGORY_ATTEMPTS", "2"))))
SCAN_CATEGORY_RETRY_SECONDS = max(1.0, min(30.0, float(os.getenv("SCAN_CATEGORY_RETRY_SECONDS", "4"))))
SUBSCRIPTION_NOTICE_POLL_SECONDS = max(60, int(os.getenv("SUBSCRIPTION_NOTICE_POLL_SECONDS", "300")))

# Public view counts are collected inline while category pages are scanned.
# Recent values are cached so shared/multi-user scans do not reopen the same ad.
VIEW_COUNT_CACHE_TTL_SECONDS = max(60, int(os.getenv("VIEW_COUNT_CACHE_TTL_SECONDS", "1800")))
VIEW_COUNT_CONCURRENCY = max(1, min(10, int(os.getenv("VIEW_COUNT_CONCURRENCY", "3"))))
# A control measurement must be fresh. This tiny window is only for coalescing
# truly simultaneous checks of the same IDs across users/scans; it is not a
# normal cache and cannot replace a 3/6/12h measurement.
VIEW_MEASUREMENT_REUSE_SECONDS = max(0, min(60, int(os.getenv("VIEW_MEASUREMENT_REUSE_SECONDS", "20"))))
VIEW_COUNT_EXPORT_MODES = {"newest", "all", "unique", "below_market"}

# v3.1 keeps the v3.0.7 Popularity Tracker. Every completed scan gets automatic public-view
# checkpoints. They are persisted, so a Railway restart does not lose the plan.
OBSERVATION_HOURS = (3, 6, 12)
OBSERVATION_POLL_SECONDS = max(15, int(os.getenv("OBSERVATION_POLL_SECONDS", "30")))
OBSERVATION_CONCURRENCY = max(1, min(4, int(os.getenv("OBSERVATION_CONCURRENCY", "1"))))
OBSERVATION_LATE_GRACE_MINUTES = max(5, int(os.getenv("OBSERVATION_LATE_GRACE_MINUTES", "45")))

# v3.2.8 My Scans inbox. Completed scans stay in the main list for 24 hours,
# then only their UI card is archived. Underlying scan/listing/history data remains
# intact for Popular Now, exports and future analytics.
SCAN_ARCHIVE_AFTER_HOURS = 24
SCAN_ARCHIVE_PAGE_SIZE = 8
SCAN_ARCHIVE_SWEEP_SECONDS = 15 * 60
ARCHIVABLE_SCAN_STATUSES = ("done", "partial", "cancelled", "failed")

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
    min_views = State()
    view_test_url = State()


class ScanInput(StatesGroup):
    target_date = State()


class AdminInput(StatesGroup):
    user_search = State()
    plan_price = State()
    custom_days = State()


def allowed(user_id: int) -> bool:
    return has_access(int(user_id), ADMIN_IDS)


def main_keyboard(selected_count: int = 0, *, admin: bool = False) -> InlineKeyboardMarkup:
    """Product-style home screen with one clear primary action."""
    rows = [
        [InlineKeyboardButton(text="▶️ Новый скан", callback_data="start_scan")],
        [InlineKeyboardButton(text="🔥 Популярное", callback_data="popular_now"),
         InlineKeyboardButton(text="📊 Мои сканы", callback_data="my_scans")],
        [InlineKeyboardButton(text=f"🗂 Категории · {selected_count}/{MAX_SELECTED_CATEGORIES}", callback_data="groups"),
         InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="💎 Подписка", callback_data="subscription")],
    ]
    if admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="adminhome")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def post_scan_keyboard(scan_id: int | None = None, *, recheck: bool = False) -> InlineKeyboardMarkup:
    """Short result actions: analytics first, secondary actions inside scan details."""
    rows = []
    if scan_id is not None:
        rows.append([
            InlineKeyboardButton(text="🔥 Открыть TOP", callback_data=f"scantop:{scan_id}"),
            InlineKeyboardButton(text="📊 Открыть скан", callback_data=f"scan:{scan_id}"),
        ])
        if recheck:
            rows.append([InlineKeyboardButton(text="🔄 Допроверить категории", callback_data=f"scanrecheck:{scan_id}")])
        rows.append([InlineKeyboardButton(text="🔄 Повторить скан", callback_data=f"scanrepeat:{scan_id}")])
    else:
        rows.append([InlineKeyboardButton(text="▶️ Новый скан", callback_data="start_scan")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="post_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def partial_recheck_keyboard(scan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Допроверить проблемные категории", callback_data=f"scanrecheck:{scan_id}")],
        [InlineKeyboardButton(text="📊 Открыть сохранённый скан", callback_data=f"scan:{scan_id}")],
    ])


def scan_detail_keyboard(scan_id: int, *, archived: bool = False, recheck: bool = False) -> InlineKeyboardMarkup:
    """Compact scan actions with analytics grouped together."""
    back_text = "⬅️ Архив" if archived else "⬅️ Мои сканы"
    back_callback = "scan_archive:0" if archived else "my_scans"
    rows = [
        [InlineKeyboardButton(text="🔥 Топ просмотров", callback_data=f"scantop:{scan_id}"),
         InlineKeyboardButton(text="🚀 Топ роста", callback_data=f"scangrowth:{scan_id}:3")],
        [InlineKeyboardButton(text="👁 Обновить", callback_data=f"scanviews:{scan_id}"),
         InlineKeyboardButton(text="📄 CSV", callback_data=f"scanexport:{scan_id}")],
    ]
    if recheck:
        rows.append([InlineKeyboardButton(text="🔄 Допроверить категории", callback_data=f"scanrecheck:{scan_id}")])
    rows += [
        [InlineKeyboardButton(text="🔄 Повторить", callback_data=f"scanrepeat:{scan_id}"),
         InlineKeyboardButton(text="🕘 История", callback_data=f"scanhistory:{scan_id}")],
        [InlineKeyboardButton(text=back_text, callback_data=back_callback),
         InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def growth_period_keyboard(scan_id: int, active_hours: int = 3, category_key: str | None = None) -> InlineKeyboardMarkup:
    prefix = f"pcg:{scan_id}:{category_key}:" if category_key else f"scangrowth:{scan_id}:"
    export_prefix = f"pce:{scan_id}:{category_key}:" if category_key else f"scangrowthexport:{scan_id}:"
    def b(hours: int) -> InlineKeyboardButton:
        label = f"{hours}ч"
        if hours == active_hours:
            label = "✅ " + label
        return InlineKeyboardButton(text=label, callback_data=f"{prefix}{hours}")

    back_callback = f"popularcat:{category_key}" if category_key else f"scan:{scan_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [b(3), b(6), b(12)],
        [InlineKeyboardButton(text="📊 Скачать TOP-50", callback_data=f"{export_prefix}{active_hours}")],
        [InlineKeyboardButton(
            text="👁 Обновить последний скан" if category_key else "👁 Обновить сейчас",
            callback_data=f"scanviews:{scan_id}",
        )],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)],
    ])


def popular_categories_keyboard(items: list[tuple[str, UserScan]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not items:
        rows.append([InlineKeyboardButton(text="▶️ Сделать первый скан", callback_data="start_scan")])
    for key, scan in items[:30]:
        cat = CATEGORIES.get(key)
        if cat is None:
            continue
        icon = GROUPS.get(cat.group).icon if cat.group in GROUPS else "📂"
        rows.append([InlineKeyboardButton(text=f"{icon} {cat.name[:38]}", callback_data=f"popularcat:{key}")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def popular_category_keyboard(scan_id: int, category_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 По просмотрам", callback_data=f"pcv:{scan_id}:{category_key}")],
        [InlineKeyboardButton(text="🚀 3ч", callback_data=f"pcg:{scan_id}:{category_key}:3"),
         InlineKeyboardButton(text="🚀 6ч", callback_data=f"pcg:{scan_id}:{category_key}:6"),
         InlineKeyboardButton(text="🚀 12ч", callback_data=f"pcg:{scan_id}:{category_key}:12")],
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
    counter_icon = "⚠️" if len(selected_keys) > MAX_SELECTED_CATEGORIES else "✅"
    rows.append([InlineKeyboardButton(
        text=f"{counter_icon} Выбрано: {len(selected_keys)}/{MAX_SELECTED_CATEGORIES}",
        callback_data="selected",
    )])
    rows.append([InlineKeyboardButton(text="🧹 Очистить выбор", callback_data="clear_all")])
    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_keyboard(group_key: str, selected_keys: set[str]) -> InlineKeyboardMarkup:
    cats = categories_for_group(group_key)
    rows = [[InlineKeyboardButton(
        text=("⚠️" if len(selected_keys) > MAX_SELECTED_CATEGORIES else "✅")
             + f" Выбрано: {len(selected_keys)}/{MAX_SELECTED_CATEGORIES}",
        callback_data="selected",
    )]]
    for cat in cats:
        marker = "✅" if cat.key in selected_keys else "▫️"
        rows.append([InlineKeyboardButton(text=f"{marker} {cat.name}", callback_data=f"cat:{cat.key}")])
    child_keys = [c.key for c in cats if not c.is_group]
    selected_children = [k for k in child_keys if k in selected_keys]
    bulk_label = "🧹 Убрать выбранные в разделе" if selected_children else f"☑️ Выбрать до {MAX_SELECTED_CATEGORIES}"
    rows.append([InlineKeyboardButton(text=bulk_label, callback_data=f"grpall:{group_key}")])
    rows.append([InlineKeyboardButton(text="▶️ Новый скан", callback_data="start_scan")])
    rows.append([InlineKeyboardButton(text="⬅️ К разделам", callback_data="groups")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mode_button(s: UserSettings, mode: str) -> InlineKeyboardButton:
    label = MODE_LABELS[mode]
    if s.output_mode == mode:
        label = "✅ " + label
    return InlineKeyboardButton(text=label, callback_data=f"quickmode:{mode}")


def min_views_label(value: int | None) -> str:
    threshold = max(0, int(value or 0))
    return "Без порога" if threshold == 0 else f"{threshold}+"


def settings_keyboard(s: UserSettings) -> InlineKeyboardMarkup:
    mode_label = MODE_LABELS.get(s.output_mode, s.output_mode)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Режим: {mode_label}", callback_data="set_mode")],
        [InlineKeyboardButton(text=f"💶 {PRICE_LABELS.get(s.price_filter, s.price_filter)}", callback_data="set_price"),
         InlineKeyboardButton(text=f"👁 {min_views_label(getattr(s, 'min_views', 0))}", callback_data="set_min_views")],
        [InlineKeyboardButton(text=f"🧠 Дубли · {'Вкл' if s.smart_dedupe else 'Выкл'}", callback_data="toggle_dedupe"),
         InlineKeyboardButton(text=f"🧹 Шум · {'Вкл' if s.clean_noise else 'Выкл'}", callback_data="toggle_noise")],
        [InlineKeyboardButton(text=f"↕️ {SORT_LABELS.get(s.sort_mode, s.sort_mode)}", callback_data="set_sort")],
        [InlineKeyboardButton(text="🔎 Ключевые слова", callback_data="set_include"),
         InlineKeyboardButton(text="🚫 Исключения", callback_data="set_exclude")],
        [InlineKeyboardButton(text="▶️ Новый скан", callback_data="start_scan")],
        [InlineKeyboardButton(text="ℹ️ О режимах", callback_data="mode_help"),
         InlineKeyboardButton(text="♻️ Сбросить", callback_data="reset_settings")],
        [InlineKeyboardButton(text="⬅️ Меню", callback_data="home")],
    ])


def page_limit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="25 стр.", callback_data="scanpages:25"),
         InlineKeyboardButton(text="50 стр.", callback_data="scanpages:50"),
         InlineKeyboardButton(text="100 стр.", callback_data="scanpages:100")],
        [InlineKeyboardButton(text="⬅️ Другая дата", callback_data="start_scan"),
         InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
    ])


def scan_date_keyboard() -> InlineKeyboardMarkup:
    today = datetime.now(MOSCOW).date()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📅 Сегодня · {today:%d.%m}", callback_data="scan_date:today"),
         InlineKeyboardButton(text=f"↩️ Вчера · {yesterday:%d.%m}", callback_data="scan_date:yesterday")],
        [InlineKeyboardButton(text="🗓 Выбрать дату", callback_data="scan_date:custom")],
        [InlineKeyboardButton(text="⬅️ Меню", callback_data="home")],
    ])


def choice_keyboard(prefix: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"{prefix}:{value}")] for value, label in options]
    rows.append([InlineKeyboardButton(text="⬅️ К настройкам", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def min_views_keyboard(current: int = 0) -> InlineKeyboardMarkup:
    presets = (0, 10, 25, 50, 100)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for value in presets:
        label = "Без порога" if value == 0 else f"{value}+"
        if int(current or 0) == value:
            label = "✅ " + label
        row.append(InlineKeyboardButton(text=label, callback_data=f"minviews:{value}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Своё значение", callback_data="minviews:custom")])
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


def _validate_scan_category_count(category_keys: list[str]) -> list[str]:
    return validate_scan_category_keys(category_keys, set(CATEGORIES))


async def create_user_scan(user_id: int, job_uid: str, category_keys: list[str], page_limit: int, target_date: str) -> UserScan:
    category_keys = _validate_scan_category_count(category_keys)
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


async def attach_user_scan_message(scan_id: int, chat_id: int, status_message_id: int) -> None:
    async with SessionLocal() as session:
        scan = await session.get(UserScan, int(scan_id))
        if scan is None:
            return
        scan.chat_id = int(chat_id)
        scan.status_message_id = int(status_message_id)
        await session.commit()


async def record_user_scan_retry(scan_id: int | None, error_text: str) -> None:
    if scan_id is None:
        return
    async with SessionLocal() as session:
        scan = await session.get(UserScan, int(scan_id))
        if scan is None:
            return
        scan.retry_count = int(scan.retry_count or 0) + 1
        scan.last_error = (error_text or "")[:1000] or None
        await session.commit()


async def get_user_scan(user_id: int, scan_id: int) -> UserScan | None:
    async with SessionLocal() as session:
        result = await session.execute(select(UserScan).where(UserScan.id == scan_id, UserScan.user_id == user_id))
        return result.scalar_one_or_none()


async def archive_expired_scans(user_id: int | None = None) -> int:
    """Archive completed scan cards 24h after completion without deleting data."""
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=SCAN_ARCHIVE_AFTER_HOURS)
    conditions = [
        UserScan.archived_at.is_(None),
        UserScan.status.in_(ARCHIVABLE_SCAN_STATUSES),
        func.coalesce(UserScan.finished_at, UserScan.created_at) <= cutoff,
    ]
    if user_id is not None:
        conditions.append(UserScan.user_id == user_id)
    async with db_write_lock:
        async with SessionLocal() as session:
            result = await session.execute(
                update(UserScan).where(*conditions).values(archived_at=now)
            )
            await session.commit()
            return int(result.rowcount or 0)


async def archive_active_finished_scans(user_id: int) -> int:
    """Manual inbox cleanup: archive every finished visible scan immediately."""
    now = datetime.utcnow()
    async with db_write_lock:
        async with SessionLocal() as session:
            result = await session.execute(
                update(UserScan)
                .where(
                    UserScan.user_id == user_id,
                    UserScan.archived_at.is_(None),
                    UserScan.status.in_(ARCHIVABLE_SCAN_STATUSES),
                )
                .values(archived_at=now)
            )
            await session.commit()
            return int(result.rowcount or 0)


async def get_user_scans(user_id: int, limit: int = 10) -> list[UserScan]:
    await archive_expired_scans(user_id)
    async with SessionLocal() as session:
        result = await session.execute(
            select(UserScan)
            .where(UserScan.user_id == user_id, UserScan.archived_at.is_(None))
            .order_by(UserScan.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def get_user_archive(user_id: int, page: int = 0, page_size: int = SCAN_ARCHIVE_PAGE_SIZE) -> tuple[list[UserScan], int]:
    await archive_expired_scans(user_id)
    page = max(0, int(page))
    async with SessionLocal() as session:
        total = int((await session.execute(
            select(func.count(UserScan.id)).where(
                UserScan.user_id == user_id, UserScan.archived_at.is_not(None)
            )
        )).scalar_one())
        result = await session.execute(
            select(UserScan)
            .where(UserScan.user_id == user_id, UserScan.archived_at.is_not(None))
            .order_by(UserScan.archived_at.desc(), UserScan.created_at.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total


async def get_archive_count(user_id: int) -> int:
    await archive_expired_scans(user_id)
    async with SessionLocal() as session:
        return int((await session.execute(
            select(func.count(UserScan.id)).where(
                UserScan.user_id == user_id, UserScan.archived_at.is_not(None)
            )
        )).scalar_one())


async def get_user_popular_categories(user_id: int, limit_scans: int = 100) -> list[tuple[str, UserScan]]:
    """Return one menu entry per category using its latest successful scan only."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(UserScan)
            .where(
                UserScan.user_id == user_id,
                UserScan.status == "done",
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


async def get_user_category_scans(user_id: int, category_key: str) -> list[UserScan]:
    """Return successful scans containing category_key, newest first.

    This remains available for history/admin features. «Популярное сейчас» uses
    only the first item (the latest successful scan).
    """
    if category_key not in CATEGORIES:
        return []
    async with SessionLocal() as session:
        result = await session.execute(
            select(UserScan)
            .where(
                UserScan.user_id == user_id,
                UserScan.status == "done",
            )
            .order_by(UserScan.finished_at.desc(), UserScan.created_at.desc())
        )
        scans = list(result.scalars().all())
    return [scan for scan in scans if category_key in _scan_category_keys(scan)]


async def get_latest_scan_for_category(user_id: int, category_key: str) -> UserScan | None:
    scans = await get_user_category_scans(user_id, category_key)
    return scans[0] if scans else None


async def get_category_scan_rows(user_id: int, category_key: str) -> tuple[list[Listing], list[UserScan]]:
    """Return listings from the latest successful scan of a category only."""
    scan = await get_latest_scan_for_category(user_id, category_key)
    if scan is None:
        return [], []
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing)
            .join(ScanListing, Listing.external_id == ScanListing.external_id)
            .where(
                ScanListing.scan_id == scan.id,
                Listing.category_key == category_key,
                Listing.is_promoted.is_(False),
            )
        )
        rows = list(result.scalars().all())
    return rows, [scan]


def _category_scan_dates(scans: list[UserScan], max_items: int = 8) -> str:
    dates = sorted({scan.target_date for scan in scans if scan.target_date}, reverse=True)
    if not dates:
        return "—"
    shown = [_date_label(value) for value in dates[:max_items]]
    if len(dates) > max_items:
        shown.append(f"+ ещё {len(dates) - max_items}")
    return ", ".join(shown)


async def get_scan_rows(scan_id: int) -> list[tuple[Listing, ScanListing]]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing, ScanListing)
            .join(ScanListing, Listing.external_id == ScanListing.external_id)
            .where(ScanListing.scan_id == scan_id)
        )
        return list(result.all())


async def ensure_scan_observation_plan(scan_id: int, finished_at: datetime | None = None) -> None:
    """Create the +3/+6/+12h plan once; safe to call after restarts."""
    async with db_write_lock:
        async with SessionLocal() as session:
            scan = await session.get(UserScan, scan_id)
            if scan is None or scan.status not in {"done", "partial"}:
                return
            base = finished_at or scan.finished_at or scan.created_at
            # Drop obsolete unfinished +1h/+24h checkpoints left by older versions.
            # Completed history remains available, but it is no longer scheduled or shown.
            await session.execute(
                delete(ScanObservation).where(
                    ScanObservation.scan_id == scan_id,
                    ScanObservation.target_hours.notin_(OBSERVATION_HOURS),
                    ScanObservation.status.in_(["pending", "error", "missed"]),
                )
            )
            existing = await session.execute(
                select(ScanObservation.target_hours).where(
                    ScanObservation.scan_id == scan_id,
                    ScanObservation.target_hours.in_(OBSERVATION_HOURS),
                )
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


async def cleanup_obsolete_observation_plans() -> int:
    """Remove unfinished +1h/+24h jobs created by pre-v3.2.8 versions."""
    async with db_write_lock:
        async with SessionLocal() as session:
            result = await session.execute(
                delete(ScanObservation).where(
                    ScanObservation.target_hours.notin_(OBSERVATION_HOURS),
                    ScanObservation.status != "done",
                )
            )
            await session.commit()
            return int(result.rowcount or 0)


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
            scan.incomplete_category_keys = ",".join(
                key for key in job.category_keys if key in (job.incomplete_category_keys or set())
            )
            quality_scores = [int(x) for x in (job.quality_scores or []) if x is not None]
            scan.quality_score = round(sum(quality_scores) / len(quality_scores)) if quality_scores else 0
            scan.quality_note = " | ".join((job.quality_notes or [])[:4])[:500]
            if not cancelled and job.incomplete_categories == 0:
                scan.last_error = None
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

            # Freeze the user's active parser settings into this saved scan.
            # The raw crawl stays in Listing for analytics, while ScanListing
            # contains only what the user asked to see at scan completion.
            scan_settings = await session.get(UserSettings, job.user_id)
            if scan_settings is None:
                scan_settings = UserSettings(user_id=job.user_id)
            rows = apply_listing_settings(
                rows, scan_settings, exact_date_scan=True, apply_output_mode=True
            )

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


async def update_scan_view_refresh(
    scan_id: int, target_hours: int | None = None, *, fresh_after: datetime | None = None
) -> int:
    """Store one real view observation round for a saved scan.

    v3.1.6 never creates a new point from arbitrary stale values already sitting
    in ``listings``. ``fresh_after`` is the start of the current measurement
    (minus the tiny simultaneous-request reuse window).
    """
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
            if fresh_after is not None:
                query = query.where(
                    Listing.views_checked_at.is_not(None),
                    Listing.views_checked_at >= fresh_after,
                )
            elif target_hours is not None:
                # Compatibility safety for older callers.
                query = query.where(
                    Listing.views_checked_at.is_not(None),
                    Listing.views_checked_at >= now - timedelta(minutes=2),
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
            if recorded > 0:
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


async def get_latest_manual_growth_summary(scan_id: int) -> tuple[int, int, int]:
    """Return (grew_count, max_delta, total_delta) for the latest real round.

    The comparison is against the immediately previous observation point for
    each listing (or the initial scan snapshot when this is the first refresh).
    """
    pairs = await get_scan_rows(scan_id)
    if not pairs:
        return 0, 0, 0
    baseline = {
        listing.external_id: (snap.captured_at, int(snap.initial_view_count))
        for listing, snap in pairs if snap.initial_view_count is not None
    }
    ids = list(baseline)
    if not ids:
        return 0, 0, 0
    async with SessionLocal() as session:
        result = await session.execute(
            select(
                ScanViewHistory.external_id,
                ScanViewHistory.view_count,
                ScanViewHistory.recorded_at,
            )
            .where(
                ScanViewHistory.scan_id == scan_id,
                ScanViewHistory.external_id.in_(ids),
            )
            .order_by(ScanViewHistory.external_id, ScanViewHistory.recorded_at)
        )
        points = list(result.all())

    by_id: dict[str, list[tuple[datetime, int]]] = {}
    for external_id, (captured_at, initial_views) in baseline.items():
        by_id[external_id] = [(captured_at, initial_views)]
    for external_id, view_count, recorded_at in points:
        by_id.setdefault(external_id, []).append((recorded_at, int(view_count)))

    grew = 0
    max_delta = 0
    total_delta = 0
    for series in by_id.values():
        series.sort(key=lambda item: item[0])
        # Deduplicate an identical timestamp/value pair defensively.
        compact: list[tuple[datetime, int]] = []
        for point in series:
            if not compact or point != compact[-1]:
                compact.append(point)
        if len(compact) < 2:
            continue
        delta = compact[-1][1] - compact[-2][1]
        if delta > 0:
            grew += 1
            total_delta += delta
            max_delta = max(max_delta, delta)
    return grew, max_delta, total_delta


async def get_scan_growth_rows(
    scan_id: int, period_hours: int, category_key: str | None = None
) -> tuple[list[GrowthMetric], int]:
    """Return TOP growth, sorted by real absolute view increase.

    If an automatic checkpoint exists for the requested horizon, compare it to
    the initial scan snapshot. Otherwise fall back to the closest manual history
    so old scans remain useful.
    """
    period_hours = period_hours if period_hours in set(OBSERVATION_HOURS) else 3
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


async def get_category_growth_rows(
    user_id: int, category_key: str, period_hours: int
) -> tuple[list[GrowthMetric], int, int]:
    """Return growth metrics for the latest successful scan of a category only."""
    scan = await get_latest_scan_for_category(user_id, category_key)
    if scan is None:
        return [], 0, 0
    growth, rounds = await get_scan_growth_rows(
        scan.id, period_hours, category_key=category_key
    )
    return growth[:GROWTH_TOP_LIMIT], 1, rounds


def _scan_list_button(scan: UserScan) -> InlineKeyboardButton:
    icon = (
        "✅" if scan.status == "done"
        else "⚠️" if scan.status == "partial"
        else "⏳" if scan.status in {"queued", "running"}
        else "⏹" if scan.status in {"cancelling", "cancelled"}
        else "❌" if scan.status == "failed"
        else "⚪️"
    )
    target_label = _date_label(scan.target_date) if scan.target_date else _moscow_text(scan.finished_at or scan.created_at)[:10]
    label = f"{icon} {scan.title[:22]} · {target_label[:5]}"
    return InlineKeyboardButton(text=label, callback_data=f"scan:{scan.id}")


def my_scans_keyboard(scans: list[UserScan], archive_count: int = 0) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for scan in scans[:SCAN_ARCHIVE_PAGE_SIZE]:
        rows.append([_scan_list_button(scan)])
    if any(scan.status in ARCHIVABLE_SCAN_STATUSES for scan in scans):
        rows.append([InlineKeyboardButton(
            text="🧹 Очистить и переместить в архив", callback_data="archive_my_scans"
        )])
    rows.append([InlineKeyboardButton(text=f"📦 Архив · {archive_count}", callback_data="scan_archive:0")])
    rows.append([InlineKeyboardButton(text="▶️ Новый скан", callback_data="start_scan")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def scan_archive_keyboard(scans: list[UserScan], page: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [[_scan_list_button(scan)] for scan in scans]
    max_page = max(0, (max(0, total - 1)) // SCAN_ARCHIVE_PAGE_SIZE)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"scan_archive:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{max_page + 1}", callback_data="archive_noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"scan_archive:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Мои сканы", callback_data="my_scans")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _replace_selected_categories(user_id: int, keys: set[str]) -> set[str]:
    clean = [key for key in CATEGORIES if key in keys][:MAX_SELECTED_CATEGORIES]
    async with SessionLocal() as session:
        await session.execute(delete(SelectedCategory).where(SelectedCategory.user_id == user_id))
        session.add_all([SelectedCategory(user_id=user_id, category_key=key) for key in clean])
        await session.commit()
    return set(clean)


async def toggle_category(user_id: int, key: str) -> tuple[set[str], bool]:
    """Toggle one category without ever allowing a new selection above 5.

    Returns (selected, limit_reached). Removing a category is always allowed.
    Choosing a group-root replaces its child selections; choosing a child replaces
    the root, so those operations do not consume an extra slot unnecessarily.
    """
    cat = CATEGORIES[key]
    selected = await get_selected(user_id)
    updated, limit_reached = toggle_selection(
        selected,
        key,
        is_group=bool(cat.is_group),
        root_key=group_root_key(cat.group),
        child_keys={c.key for c in categories_for_group(cat.group) if not c.is_group},
    )
    if limit_reached:
        return selected, True
    return await _replace_selected_categories(user_id, updated), False


async def toggle_group_children(user_id: int, group_key: str) -> tuple[set[str], bool]:
    """Bulk-select only the remaining free slots, or clear this group's children."""
    child_keys = [c.key for c in categories_for_group(group_key) if not c.is_group]
    selected = await get_selected(user_id)
    updated, limit_reached = bulk_group_selection(
        selected, child_keys, root_key=group_root_key(group_key)
    )
    return await _replace_selected_categories(user_id, updated), limit_reached


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
                    is_active=True, is_promoted=False, disappeared_at=None,
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
                row.is_promoted = False
                row.disappeared_at = None
        await session.commit()
        return new_items, len(unique) - len(new_items), enriched_count


async def mark_promoted_listings(external_ids: list[str] | set[str]) -> int:
    """Hide paid-visibility ads that were already stored by an older parser run.

    We do not create rows for promoted-only cards. Existing rows are marked so
    today's global exports/popular lists stop showing them immediately. If the
    feature expires, a later organic sighting resets is_promoted in upsert_page_items.
    """
    ids = {str(x).strip() for x in external_ids if str(x).strip()}
    if not ids:
        return 0
    async with db_write_lock:
        async with SessionLocal() as session:
            result = await session.execute(select(Listing).where(Listing.external_id.in_(list(ids))))
            rows = list(result.scalars().all())
            changed = 0
            for row in rows:
                if not getattr(row, "is_promoted", False):
                    row.is_promoted = True
                    changed += 1
            if changed:
                await session.commit()
            return changed


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
            Listing.is_promoted.is_(False),
        ))
        return list(result.scalars().all())


async def filtered_rows(user_id: int) -> tuple[UserSettings, list[Listing]]:
    s = await get_settings(user_id)
    rows = await today_rows()
    rows = base_filter(
        rows, period=None, price_filter=s.price_filter, clean_noise=s.clean_noise,
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
            "Категория", "Название", "Цена", "Цена, €", "👁 Просмотры",
            "Дата (МСК)", "Как показано на Kleinanzeigen", "Ссылка"
        ])
        for row in rows:
            writer.writerow([
                row.category, row.title,
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
        browser_fallback=False,
        direct_http_only=True,
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
    progress_message: Message | None = None,
    progress_title: str | None = None,
) -> tuple[int, int, int]:
    """Refresh missing/stale public view counters and persist them.

    Returns (requested, updated, failed). max_age_seconds lets automatic checkpoints
    safely reuse a counter fetched only a few minutes ago by another scan/user.
    """
    if not rows:
        return 0, 0, 0

    effective_ttl = VIEW_COUNT_CACHE_TTL_SECONDS if max_age_seconds is None else max(0, int(max_age_seconds))
    cutoff = datetime.utcnow() - timedelta(seconds=effective_ttl)
    eligible = [row for row in rows if row.url]
    targets = [
        row for row in eligible
        if force or row.views_checked_at is None or row.views_checked_at < cutoff
    ]
    reused_count = max(0, len(eligible) - len(targets))

    status = progress_message
    status_note = "свежий контрольный замер" if effective_ttl <= 60 else f"кэш {max(1, effective_ttl // 60)} мин."
    if status is None and message is not None:
        try:
            status = await message.answer(
                f"👁 Собираю просмотры для <b>{len(eligible)}</b> объявлений…\n"
                f"⚡ Лёгкий прямой счётчик · без browser fallback · {status_note}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            status = None

    last_progress_edit = 0.0

    async def progress_cb(done: int, total: int):
        nonlocal last_progress_edit
        if status is not None and hasattr(status, "edit_text"):
            now_mono = time.monotonic()
            completed = min(len(eligible), reused_count + done)
            all_total = max(1, len(eligible))
            if completed < all_total and now_mono - last_progress_edit < 1.5:
                return
            last_progress_edit = now_mono
            try:
                pct = round(completed / all_total * 100) if eligible else 100
                title = progress_title or "👁 Собираю просмотры"
                await status.edit_text(
                    f"<b>{html.escape(title)}</b>\n\n"
                    f"{_progress_bar(pct)} <b>{pct}%</b>\n"
                    f"📦 Проверено: <b>{completed}/{len(eligible)}</b>\n"
                    f"⚡ Direct-запросы: <b>{done}/{len(targets)}</b>\n"
                    f"♻️ Уже свежих: <b>{reused_count}</b>\n\n"
                    "Можно пользоваться другими разделами — замер идёт в фоне.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    if status is not None:
        await progress_cb(0, len(targets))
    if not targets:
        return 0, 0, 0

    parser = KleinanzeigenParser()
    try:
        results = await parser.fetch_public_view_counts(
            [row.url for row in targets],
            concurrency=VIEW_COUNT_CONCURRENCY,
            progress_cb=progress_cb,
            traffic_priority=traffic_priority,
            browser_fallback=False,
            direct_http_only=True,
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
                        ScanObservation.target_hours.in_(OBSERVATION_HOURS),
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
        measurement_started = datetime.utcnow() - timedelta(seconds=VIEW_MEASUREMENT_REUSE_SECONDS)
        async with background_view_refresh_lock:
            requested, updated, failed = await refresh_view_counts(
                rows, None, force=False, max_age_seconds=VIEW_MEASUREMENT_REUSE_SECONDS,
                traffic_priority="background"
            )
            recorded = await update_scan_view_refresh(
                scan.id, target_hours=obs.target_hours, fresh_after=measurement_started
            )
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
    """Persistent +3/+6/+12h view-checkpoint worker."""
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


async def scan_archive_scheduler() -> None:
    """Keep the My Scans inbox compact even when the user does not open it."""
    while True:
        try:
            moved = await archive_expired_scans()
            if moved:
                log.info("Auto-archived %s completed scan cards", moved)
            await asyncio.sleep(SCAN_ARCHIVE_SWEEP_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scan archive scheduler loop error")
            await asyncio.sleep(SCAN_ARCHIVE_SWEEP_SECONDS)


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
        all_rows, period=None, price_filter=s.price_filter, clean_noise=s.clean_noise,
        include_words=s.include_words or "", exclude_words=s.exclude_words or "",
    )

    # Frequency intentionally sees all distinct IDs; otherwise smart de-duplication
    # would hide the very repetitions this mode is meant to measure. Unique is
    # also evaluated on raw filtered rows so duplicates cannot turn into a fake
    # unique product after being collapsed.
    if mode == "unique":
        base = unique_rows(raw_base)
    else:
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
            all_rows, period=None, price_filter=s.price_filter, clean_noise=s.clean_noise,
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
        result = sort_rows(base, s.sort_mode)
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
        "<b>⚙️ Настройки</b>\n\n"
        f"Режим: <b>{MODE_LABELS.get(s.output_mode, s.output_mode)}</b>\n"
        f"💶 Цена: <b>{PRICE_LABELS.get(s.price_filter, s.price_filter)}</b>\n"
        f"👁 Просмотры: <b>{min_views_label(getattr(s, 'min_views', 0))}</b>\n"
        f"🧠 Умные дубли: <b>{'Вкл' if s.smart_dedupe else 'Выкл'}</b>\n"
        f"🧹 Очистка шума: <b>{'Вкл' if s.clean_noise else 'Выкл'}</b>\n"
        f"↕️ Сортировка: <b>{SORT_LABELS.get(s.sort_mode, s.sort_mode)}</b>\n"
        f"🔎 Ключевые слова: <b>{include}</b>\n"
        f"🚫 Исключения: <b>{exclude}</b>\n\n"
        "<i>Дата выбирается отдельно при запуске нового скана.</i>"
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
    storage = DATABASE_BACKEND
    warning = ""
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
    incomplete_category_keys: set[str] | None = None
    retry_note: str = ""
    recovered: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)


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
    # Number of real category-page HTTP responses already processed.  This gives
    # the UI a truthful heartbeat during date-location before collection starts.
    network_requests: int = 0


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
category_inflight_waiters: dict[str, int] = {}
category_inflight_guard = asyncio.Lock()
# Exact-date cache must preserve the exact 25/50/100-page result set, so v3.0.6
# caches ScanResult (including matched IDs) in memory instead of reconstructing a
# result from every listing ever seen for that date.
category_result_cache: dict[str, tuple[float, ScanResult]] = {}
db_write_lock = asyncio.Lock()

# v3.1.6 manual view refreshes are true background jobs. A user can navigate
# anywhere in the bot while the refresh continues, and duplicate refreshes of the
# same scan are coalesced into one task.
manual_view_tasks: dict[int, asyncio.Task] = {}
manual_view_tasks_guard = asyncio.Lock()
# One lightweight background collector at a time. This makes DB cache reuse deterministic
# across users/scans and prevents automatic checkpoints from multiplying the same ID requests.
background_view_refresh_lock = asyncio.Lock()


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
            live.network_requests = max(live.network_requests, network_requests)
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
        # v3.1.8: use Kleinanzeigen's official Anbieter=Privat filter at the
        # search-feed level. Commercial/store listings therefore never consume
        # scan depth and never enter snapshots, views or TOP analytics.
        base_url = private_provider_url(base_url)
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
                promoted_ids = list(getattr(info, "promoted_ids", None) or [])
                if promoted_ids:
                    await mark_promoted_listings(promoted_ids)
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

        # v3.3.1: hidden/regional fallback measures depth by real verified result
        # pages, not by how many listings survive seller/promotion/dedupe filters.
        # A Kleinanzeigen page can contain far fewer than 25 usable private ads;
        # treating 25 surviving rows as one page caused false "partial" warnings.
        hidden_pages_collected = 0
        goal_pages = max(0, int(remaining_virtual_pages))
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

        while queue and hidden_pages_collected < goal_pages and feeds_processed < max_hidden_feeds:
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
            while page <= feed_limit and hidden_pages_collected < goal_pages:
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
                # Count the actual verified page even when business/promoted cards
                # were filtered out and only a handful of usable listings remain.
                target_on_page = any(d == target_day for d in days)
                if target_on_page:
                    await process_target_items(items, pairs)
                    hidden_pages_collected += 1
                    update_live(
                        page, days, "collecting",
                        direct_pages_collected + hidden_pages_collected,
                    )
                page += 1
                if PAGE_DELAY_SECONDS:
                    await asyncio.sleep(min(PAGE_DELAY_SECONDS, 0.15))

            if page > feed_limit and not state_exhausted and hidden_pages_collected < goal_pages:
                # The target day continues beyond this feed's visible window. Drill
                # down again instead of declaring the category empty/skipped.
                if not add_children(state_name, loc, level):
                    unresolved = True

        if hidden_pages_collected >= goal_pages:
            request_complete = True
            reason = f"проверено {depth} реальных страниц выбранной даты"
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
        [InlineKeyboardButton(text="⏹ Остановить парсер", callback_data=f"cancel_scan:{job_id}")],
    ])


def stopped_job_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 Выбрать категории", callback_data="groups")],
        [InlineKeyboardButton(text="▶️ Новый скан", callback_data="start_scan")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
    ])


def failed_job_keyboard(scan_id: int | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if scan_id is not None:
        rows.append([InlineKeyboardButton(text="🔄 Повторить этот скан", callback_data=f"scanrepeat:{scan_id}")])
    rows.append([InlineKeyboardButton(text="📊 Мои сканы", callback_data="my_scans")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


async def dispatch_category(
    cat,
    user_id: int,
    page_limit: int,
    target_date: str,
    *,
    stop_event: asyncio.Event | None = None,
) -> CategoryDispatchResult:
    """Reuse only results with the same category + date + requested depth.

    Each user job is a subscriber to the shared category task. Pressing Stop
    detaches that job immediately. If it was the last subscriber, the actual
    network parser task is cancelled too; if another user still needs the same
    shared scan, their work is left untouched.
    """
    inflight_key = _progress_key(cat.key, target_date, page_limit)

    if stop_event is not None and stop_event.is_set():
        raise ScanStopRequested()

    if CATEGORY_CACHE_TTL_SECONDS > 0:
        cached = category_result_cache.get(inflight_key)
        if cached is not None:
            cached_at, cached_result = cached
            age = max(0, int(time.monotonic() - cached_at))
            if age <= CATEGORY_CACHE_TTL_SECONDS:
                if stop_event is not None and stop_event.is_set():
                    raise ScanStopRequested()
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
        category_inflight_waiters[inflight_key] = category_inflight_waiters.get(inflight_key, 0) + 1

    stopped = False
    cancel_underlying = False
    try:
        result = await wait_for_task_or_stop(task, stop_event)
        if CATEGORY_CACHE_TTL_SECONDS > 0:
            category_result_cache[inflight_key] = (time.monotonic(), result)
        return CategoryDispatchResult(source=source, result=result)
    except ScanStopRequested:
        stopped = True
        raise
    finally:
        async with category_inflight_guard:
            remaining = max(0, category_inflight_waiters.get(inflight_key, 1) - 1)
            if remaining:
                category_inflight_waiters[inflight_key] = remaining
            else:
                category_inflight_waiters.pop(inflight_key, None)
                # No one else needs this network scan. A user-requested stop must
                # terminate the HTTP/parser task rather than merely hide progress.
                if stopped and category_inflight.get(inflight_key) is task and not task.done():
                    cancel_underlying = True
                    task.cancel()

            if task.done() and category_inflight.get(inflight_key) is task:
                category_inflight.pop(inflight_key, None)

        if cancel_underlying:
            await asyncio.gather(task, return_exceptions=True)
            async with category_inflight_guard:
                if category_inflight.get(inflight_key) is task:
                    category_inflight.pop(inflight_key, None)

        if task.done() or cancel_underlying:
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
    """Compact user-facing scan progress. Technical diagnostics stay in logs/results."""
    total = max(1, len(job.category_keys))
    depth = job.page_limit if job.page_limit in PAGE_LIMIT_CHOICES else 50

    if job.state == "queued":
        waited = max(0, int((datetime.utcnow() - job.created_at).total_seconds()))
        headline = "♻️ <b>Восстанавливаю скан…</b>\n\n" if job.recovered else "⏳ <b>Подготавливаю скан</b>\n\n"
        return (
            headline
            + f"🗂 Категорий: <b>{total}</b>\n"
            + f"📅 <b>{_date_label(job.target_date)}</b> · 📄 <b>{depth} стр.</b>\n"
            + f"⏱ <b>{_human_duration(waited)}</b>"
        )

    if job.retry_note:
        return (
            "♻️ <b>Временный сбой — повторяю категорию</b>\n\n"
            f"📂 <b>{html.escape(job.current_category or 'Категория')}</b>\n"
            f"{html.escape(job.retry_note)}\n"
            "Уже собранные данные сохранены."
        )

    live = category_live_progress.get(job.current_progress_key) if job.current_progress_key else None
    current_today = live.today_seen if live is not None else 0
    live_views_ready = live.views_ready if live is not None else 0

    # v3.4.3: progress must move from the first network request, not only after
    # the date locator has finished.  Date discovery is not linear, so it owns a
    # conservative first 18% of the current category.  Collection owns the next
    # 77%; the final 5% is reserved for persistence/export.  This is a UI progress
    # estimate, not a fake page count.
    current_fraction = 0.02
    if live is not None:
        if live.phase == "collecting" and depth > 0:
            collected = min(1.0, max(0.0, live.collection_index / depth))
            current_fraction = 0.18 + 0.77 * collected
        elif live.phase in {"jumping", "seeking"}:
            request_steps = min(12, max(0, int(live.network_requests or 0)))
            current_fraction = 0.03 + 0.15 * (request_steps / 12.0)
        elif live.phase == "views":
            total_views = max(1, int(live.today_seen or 0))
            view_ratio = min(1.0, max(0.0, float(live.views_ready) / total_views))
            current_fraction = 0.95 + 0.04 * view_ratio
    percent = int(max(0.0, min(0.99, (job.completed_categories + current_fraction) / total)) * 100)
    if job.completed_categories >= total:
        percent = 100

    elapsed = 0
    if job.started_running_monotonic:
        elapsed = max(0, int(time.monotonic() - job.started_running_monotonic))

    category_line = html.escape(job.current_category) if job.current_category else "Подготовка…"
    category_index = max(1, job.current_category_index)

    # Date-location can use jumps and internal fallback feeds, so page numbers and
    # quality telemetry are intentionally hidden from the everyday UI.
    if live is None or live.phase not in {"collecting", "views"}:
        requests_text = ""
        if live is not None and live.network_requests:
            requests_text = f"\n🌐 Проверено запросов: <b>{live.network_requests}</b>"
        return (
            f"🔎 <b>Поиск даты · {percent}%</b>\n"
            f"{_progress_bar(percent)}\n\n"
            f"🗂 <b>{category_line}</b> · {category_index}/{total}\n"
            f"📅 За <b>{_date_label(job.target_date)}</b>"
            f"{requests_text}\n"
            f"⏱ <b>{_human_duration(elapsed)}</b>\n\n"
            "Запросы распределяются между активными сканами — статус обновляется автоматически."
        )

    if live.phase == "views":
        total_views = max(1, int(live.today_seen or 0))
        ready = min(total_views, int(live.views_ready or 0))
        return (
            f"👁 <b>Собираю просмотры · {percent}%</b>\n"
            f"{_progress_bar(percent)}\n\n"
            f"🗂 <b>{category_line}</b> · {category_index}/{total}\n"
            f"📦 Объявлений: <b>{live.today_seen}</b>\n"
            f"👁 Проверено: <b>{ready}/{total_views}</b>\n"
            f"⏱ <b>{_human_duration(elapsed)}</b>"
        )

    pages_done = max(0, min(depth, int(live.collection_index or 0)))
    views_text = ""
    if current_today:
        views_text = f" · 👁 <b>{min(live_views_ready, current_today)}</b>"

    return (
        f"🔎 <b>Сканирование · {percent}%</b>\n"
        f"{_progress_bar(percent)}\n\n"
        f"🗂 <b>{category_line}</b> · {category_index}/{total}\n"
        f"📄 <b>{pages_done}/{depth}</b> страниц\n"
        f"📦 <b>{current_today}</b> объявлений{views_text}\n"
        f"⏱ <b>{_human_duration(elapsed)}</b>"
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
            "⏹ <b>Скан остановлен</b>\n\n"
            f"🗂 Обработано: <b>{job.completed_categories}/{len(job.category_keys)}</b> категорий\n"
            f"📦 Найдено до остановки: <b>{job.total_new}</b>\n"
            f"⏱ Время: <b>{elapsed_text}</b>"
        )
        await edit_job_status(bot, job, text, force=True)
        try:
            await bot.edit_message_reply_markup(
                chat_id=job.chat_id,
                message_id=job.status_message_id,
                reply_markup=stopped_job_keyboard(),
            )
        except Exception:
            log.debug("Could not attach stopped-job actions job=%s", job.job_id, exc_info=True)
        return

    # Keep the live card clean while the CSV is being built, then replace it with
    # the final product-style summary after export returns the actual result count.
    await edit_job_status(
        bot,
        job,
        "✅ <b>Сканирование завершено</b>\n\n📄 Готовлю результат…",
        force=True,
    )

    result_count: int | None = None
    export_ok = False
    snapshot_rows: list[Listing] = []
    if job.scan_id is not None:
        try:
            snapshot_rows = [row for row, _ in await get_scan_rows(job.scan_id)]
        except Exception:
            log.exception("Could not load snapshot rows for job=%s", job.job_id)

    try:
        result_prefix = (
            "📄 <b>Результат скана</b>\n"
            f"📅 {_date_label(job.target_date)} · 🗂 {job.completed_categories}/{len(job.category_keys)} категорий"
        )
        adapter = BotChatAdapter(
            bot,
            job.chat_id,
            prefix=result_prefix,
            reply_markup=post_scan_keyboard(job.scan_id, recheck=bool(job.incomplete_categories)),
        )
        result_count = await send_smart_export(
            adapter,
            job.user_id,
            len(job.category_keys),
            category_keys_override=set(job.category_keys),
            rows_override=snapshot_rows,
        )
        export_ok = True
    except Exception:
        log.exception("Could not auto-export result for job=%s", job.job_id)
        try:
            await bot.send_message(
                job.chat_id,
                "⚠️ <b>Результат сохранён, но CSV не отправился.</b>\n\n"
                "Открой скан — файл можно скачать повторно.",
                parse_mode=ParseMode.HTML,
                reply_markup=post_scan_keyboard(job.scan_id, recheck=bool(job.incomplete_categories)),
            )
        except Exception:
            pass

    quality_values = [int(x) for x in (job.quality_scores or []) if x is not None]
    quality_avg = round(sum(quality_values) / len(quality_values)) if quality_values else 0
    headline = "⚠️ <b>Скан завершён частично</b>" if job.incomplete_categories else "✅ <b>Скан завершён</b>"
    lines = [
        headline,
        "",
        f"📅 Дата: <b>{_date_label(job.target_date)}</b>",
        f"🗂 Категории: <b>{job.completed_categories}/{len(job.category_keys)}</b>",
    ]
    if result_count is not None:
        lines.append(f"📦 В результате: <b>{result_count}</b>")
    if job.incomplete_categories:
        lines.append(f"⚠️ Допроверка: <b>{job.incomplete_categories}</b> категорий")
    elif quality_avg and quality_avg < 90:
        lines.append(f"🛡 Качество: <b>{quality_avg}/100</b>")
    lines.append(f"⏱ Время: <b>{elapsed_text}</b>")
    if not job.incomplete_categories:
        lines.append("🔔 Автозамеры: <b>3 · 6 · 12 ч</b>")
    lines += ["", "📄 CSV отправлен ниже." if export_ok and result_count else (
        "По текущим фильтрам подходящих объявлений нет." if export_ok else "Данные сохранены в скане."
    )]
    await edit_job_status(bot, job, "\n".join(lines), force=True)

    if job.incomplete_categories:
        try:
            await bot.send_message(
                job.chat_id,
                "<b>⚠️ Нужна допроверка</b>\n\n"
                f"{job.incomplete_categories} из {len(job.category_keys)} категорий проверены не полностью. "
                "Найденные данные сохранены.",
                parse_mode=ParseMode.HTML,
                reply_markup=partial_recheck_keyboard(job.scan_id) if job.scan_id is not None else None,
            )
        except Exception:
            log.exception("Could not send partial-scan notice for job=%s", job.job_id)
    elif job.warnings:
        log.info("scan job=%s warnings=%s", job.job_id, job.warnings[:20])


async def dispatch_category_with_retry(bot: Bot, job: ScanJob, cat) -> CategoryDispatchResult:
    """Run one category with a small job-level retry safety net.

    The parser itself already retries HTTP/403/429/5xx responses. This outer layer
    protects the user from a one-off transport/runtime failure and keeps a partial
    scan recoverable instead of immediately turning the category into an error.
    """
    last_exc: Exception | None = None
    for attempt in range(1, SCAN_CATEGORY_ATTEMPTS + 1):
        try:
            job.retry_note = ""
            return await dispatch_category(
                cat, job.user_id, job.page_limit, job.target_date, stop_event=job.stop_event
            )
        except ScanStopRequested:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            await record_user_scan_retry(job.scan_id, f"{cat.name}: {type(exc).__name__}: {exc}")
            if attempt >= SCAN_CATEGORY_ATTEMPTS:
                raise
            job.retry_note = (
                f"Попытка {attempt + 1}/{SCAN_CATEGORY_ATTEMPTS} через "
                f"{int(SCAN_CATEGORY_RETRY_SECONDS * attempt)} сек."
            )
            await edit_job_status(bot, job, render_user_job_status(job), force=True)
            await asyncio.sleep(SCAN_CATEGORY_RETRY_SECONDS * attempt)
            if job.stop_event.is_set() or job.cancel_requested:
                raise ScanStopRequested()
    assert last_exc is not None
    raise last_exc


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
    job.incomplete_category_keys = job.incomplete_category_keys or set()
    await edit_job_status(bot, job, render_user_job_status(job), force=True)

    for idx, key in enumerate(job.category_keys, start=1):
        if job.cancel_requested:
            break
        cat = CATEGORIES.get(key)
        if cat is None:
            job.warnings.append(f"Неизвестная категория: {key}")
            job.incomplete_categories += 1
            job.incomplete_category_keys = job.incomplete_category_keys or set()
            job.incomplete_category_keys.add(key)
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
            dispatched = await dispatch_category_with_retry(bot, job, cat)
            if job.cancel_requested or job.stop_event.is_set():
                raise ScanStopRequested()
            result = dispatched.result
            source_label = "♻️ кэш"
            if dispatched.source == "cache":
                job.cache_hits += 1
                source_label = f"♻️ кэш ({dispatched.cache_age_seconds} сек.)"
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
                    job.incomplete_category_keys = job.incomplete_category_keys or set()
                    job.incomplete_category_keys.add(cat.key)
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
                    job.incomplete_category_keys = job.incomplete_category_keys or set()
                    job.incomplete_category_keys.update(job.category_keys[idx:])
                    job.completed_categories += remaining_categories
                break
            # User sees only useful progress; cache/shared/worker details stay internal.
            await edit_job_status(bot, job, render_user_job_status(job), force=True)
        except ScanStopRequested:
            job.cancel_requested = True
            log.info("User stopped scan job=%s category=%s", job.job_id, cat.name)
            break
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
            job.incomplete_category_keys = job.incomplete_category_keys or set()
            job.incomplete_category_keys.add(cat.key)
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
                            scan.last_error = "Необработанная ошибка воркера; см. Railway logs"
                            await session.commit()
                except Exception:
                    log.exception("Could not mark user scan failed scan_id=%s", job.scan_id)
            try:
                await bot.edit_message_text(
                    chat_id=job.chat_id,
                    message_id=job.status_message_id,
                    text=(
                        "❌ <b>Парсер не смог завершить этот запуск</b>\n\n"
                        "Уже собранные данные в PostgreSQL не потеряны. Можно повторить этот же скан одной кнопкой."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=failed_job_keyboard(job.scan_id),
                )
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
    category_keys = _validate_scan_category_count(category_keys)
    job_uid = uuid.uuid4().hex[:12]
    scan = await create_user_scan(user_id, job_uid, category_keys, page_limit, target_date)
    status = await message.answer("⏳ <b>Подготавливаю скан…</b>", parse_mode=ParseMode.HTML)
    await attach_user_scan_message(scan.id, message.chat.id, status.message_id)
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



async def recover_interrupted_user_scans(bot: Bot) -> int:
    """Requeue unfinished scans after a Railway/process restart.

    The already collected Listing/ParserRun rows stay in PostgreSQL. Re-running
    unfinished categories is idempotent at listing level, so a restart cannot turn
    a 70-page crawl into lost data even though live in-memory progress resets.
    """
    now = datetime.utcnow()
    recovered_jobs: list[ScanJob] = []
    seen_users: set[int] = set()
    async with SessionLocal() as session:
        # A user may have pressed Stop milliseconds before a process crash. Persisted
        # 'cancelling' jobs must remain cancelled rather than resurrecting.
        cancelling = list((await session.execute(
            select(UserScan).where(UserScan.status == "cancelling", UserScan.finished_at.is_(None))
        )).scalars().all())
        for scan in cancelling:
            scan.status = "cancelled"
            scan.finished_at = now
            scan.last_error = "Остановлен пользователем перед перезапуском сервиса"

        unfinished = list((await session.execute(
            select(UserScan)
            .where(UserScan.status.in_(["queued", "running"]), UserScan.finished_at.is_(None))
            .order_by(UserScan.created_at.desc())
        )).scalars().all())

        for scan in unfinished:
            uid = int(scan.user_id)
            if uid in seen_users:
                scan.status = "failed"
                scan.finished_at = now
                scan.last_error = "Найден дублирующий незавершённый запуск после перезапуска"
                continue
            seen_users.add(uid)
            keys = [k for k in _scan_category_keys(scan) if k in CATEGORIES]
            if not keys or not scan.chat_id:
                scan.status = "failed"
                scan.finished_at = now
                scan.last_error = "Не удалось восстановить запуск: нет категории или Telegram chat_id"
                continue

            status_message_id = int(scan.status_message_id or 0)
            try:
                msg = await bot.send_message(
                    int(scan.chat_id),
                    "♻️ <b>Сервис перезапустился — восстанавливаю незавершённый скан.</b>\n\n"
                    "Уже сохранённые объявления не потеряны. Продолжаю безопасным повторным запуском.",
                    parse_mode=ParseMode.HTML,
                )
                status_message_id = msg.message_id
            except Exception:
                log.exception("Could not create recovery status message scan=%s", scan.id)
                if not status_message_id:
                    scan.status = "failed"
                    scan.finished_at = now
                    scan.last_error = "Не удалось восстановить Telegram-контекст после перезапуска"
                    continue

            scan.status = "queued"
            scan.status_message_id = status_message_id
            scan.resumed_count = int(scan.resumed_count or 0) + 1
            scan.last_error = "Автоматически восстановлен после перезапуска Railway"
            job = ScanJob(
                job_id=scan.job_uid,
                user_id=uid,
                chat_id=int(scan.chat_id),
                status_message_id=status_message_id,
                category_keys=keys,
                created_at=scan.created_at or now,
                warnings=["Скан автоматически восстановлен после перезапуска сервиса."],
                page_limit=int(scan.page_limit or 25),
                scan_id=scan.id,
                target_date=scan.target_date or _moscow_today_iso(),
                recovered=True,
            )
            recovered_jobs.append(job)
        await session.commit()

    async with job_guard:
        for job in recovered_jobs:
            active_jobs[job.user_id] = job
            queued_job_ids.append(job.job_id)
            scan_queue.put_nowait(job)
    return len(recovered_jobs)





def _utc_to_msk_text(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")


def _access_mode_label(mode: str | None = None) -> str:
    mode = mode or current_access_mode()
    return {
        "admin_only": "🔒 Только админы",
        "subscription": "💎 По подписке",
        "open": "🌍 Открытый доступ",
    }.get(mode, mode)


def _provider_label(provider: str) -> str:
    return {"cryptobot": "🤖 CryptoBot", "xrocket": "🚀 xRocket"}.get(provider, provider)


def _payment_status_label(status: str) -> str:
    return {
        "pending": "⏳ ожидает",
        "paid": "✅ оплачено",
        "expired": "⌛ истекло",
        "failed": "❌ ошибка",
        "cancelled": "❌ отменено",
    }.get(status, status)


async def subscription_text(user_id: int) -> str:
    mode = current_access_mode()
    user = await get_commerce_user(user_id)
    until = user.access_until if user else cached_access_until(user_id)
    payments = await user_payments(user_id, 10)
    pending_count = sum(1 for p in payments if p.status == "pending")

    if user_id in ADMIN_IDS:
        status = "👑 <b>Администратор — доступ без ограничений</b>"
    elif is_banned_cached(user_id):
        status = "⛔ <b>Доступ заблокирован администратором</b>"
    elif mode == "open":
        status = "🌍 <b>Сервис сейчас открыт для всех</b>"
    elif until and until > datetime.utcnow():
        left = until - datetime.utcnow()
        hours = max(0, int(left.total_seconds() // 3600))
        days = hours // 24
        rem_hours = hours % 24
        status = (
            f"✅ <b>Подписка активна до {_utc_to_msk_text(until)} МСК</b>\n"
            f"Осталось: <b>{days} дн. {rem_hours} ч.</b>\n"
            "Можно продлить заранее — новые дни прибавятся к текущему сроку."
        )
    elif mode == "admin_only":
        status = "🔒 <b>Сервис пока работает в закрытом тестовом режиме.</b>"
    else:
        status = "❌ <b>Подписка не активна</b>\nВыбери тариф ниже, чтобы открыть доступ."

    providers = providers_status()
    methods = []
    if providers["cryptobot"]:
        methods.append("CryptoBot")
    if providers["xrocket"]:
        methods.append("xRocket")
    methods_text = " · ".join(methods) if methods else "временно недоступна"
    pending_text = f"\n⏳ Ожидающих счетов: <b>{pending_count}</b>" if pending_count else ""
    admin_mode = f"\nРежим: <b>{_access_mode_label(mode)}</b>" if user_id in ADMIN_IDS else ""
    return (
        "<b>💎 Подписка</b>\n\n"
        f"{status}\n\n"
        f"Оплата: <b>{methods_text}</b>{pending_text}{admin_mode}"
    )


async def subscription_keyboard(user_id: int) -> InlineKeyboardMarkup:
    plans = await get_plans(active_only=True)
    user = await get_commerce_user(user_id)
    active = bool(user and user.access_until and user.access_until > datetime.utcnow())
    rows: list[list[InlineKeyboardButton]] = []
    if current_access_mode() == "subscription" and not is_banned_cached(user_id):
        for plan in plans:
            prefix = "🔄 Продлить · " if active else ""
            rows.append([InlineKeyboardButton(
                text=f"{prefix}{plan.title} · {plan.price_usdt:g} USDT",
                callback_data=f"buyplan:{plan.key}",
            )])
    rows.append([InlineKeyboardButton(text="💳 Мои платежи", callback_data="mypayments")])
    if allowed(user_id):
        rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_payments_keyboard(payments: list[SubscriptionPayment]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in payments[:5]:
        if p.status == "pending":
            if p.pay_url:
                rows.append([InlineKeyboardButton(text=f"💳 Открыть счёт #{p.id}", url=p.pay_url)])
            rows.append([InlineKeyboardButton(text=f"✅ Проверить счёт #{p.id}", callback_data=f"paycheck:{p.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Подписка", callback_data="subscription")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def payment_provider_keyboard(plan_key: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if provider_enabled("cryptobot"):
        rows.append([InlineKeyboardButton(text="🤖 Оплатить через CryptoBot", callback_data=f"payprovider:cryptobot:{plan_key}")])
    if provider_enabled("xrocket"):
        rows.append([InlineKeyboardButton(text="🚀 Оплатить через xRocket", callback_data=f"payprovider:xrocket:{plan_key}")])
    rows.append([InlineKeyboardButton(text="⬅️ К подписке", callback_data="subscription")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_invoice_keyboard(payment: SubscriptionPayment) -> InlineKeyboardMarkup:
    rows = []
    if payment.pay_url:
        rows.append([InlineKeyboardButton(text="💳 Открыть оплату", url=payment.pay_url)])
    rows.append([InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"paycheck:{payment.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Подписка", callback_data="subscription")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adminstats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="adminusers"),
         InlineKeyboardButton(text="💳 Платежи", callback_data="adminpayments")],
        [InlineKeyboardButton(text="🎟 Тарифы", callback_data="adminplans"),
         InlineKeyboardButton(text="🔐 Режим доступа", callback_data="adminmode")],
        [InlineKeyboardButton(text="🔎 Найти пользователя", callback_data="adminusersearch")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
    ])


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="adminhome")]
    ])


def admin_mode_keyboard() -> InlineKeyboardMarkup:
    current = current_access_mode()
    def b(mode: str, label: str) -> InlineKeyboardButton:
        prefix = "✅ " if current == mode else ""
        return InlineKeyboardButton(text=prefix + label, callback_data=f"adminsetmode:{mode}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("admin_only", "🔒 Только админы")],
        [b("subscription", "💎 По подписке")],
        [b("open", "🌍 Открытый доступ")],
        [InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="adminhome")],
    ])


def admin_users_keyboard(users: list[BotUser]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    now = datetime.utcnow()
    for user in users[:20]:
        if user.is_banned:
            icon = "⛔"
        elif user.access_until and user.access_until > now:
            icon = "✅"
        else:
            icon = "▫️"
        name = f"@{user.username}" if user.username else (user.first_name or str(user.user_id))
        rows.append([InlineKeyboardButton(text=f"{icon} {name[:28]} · {user.user_id}", callback_data=f"adminuser:{user.user_id}")])
    rows.append([InlineKeyboardButton(text="🔎 Поиск", callback_data="adminusersearch")])
    rows.append([InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="adminhome")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_keyboard(user: BotUser) -> InlineKeyboardMarkup:
    ban_label = "✅ Разблокировать" if user.is_banned else "⛔ Заблокировать"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="+1 день", callback_data=f"admingrant:{user.user_id}:1"),
         InlineKeyboardButton(text="+3 дня", callback_data=f"admingrant:{user.user_id}:3")],
        [InlineKeyboardButton(text="+7 дней", callback_data=f"admingrant:{user.user_id}:7"),
         InlineKeyboardButton(text="+30 дней", callback_data=f"admingrant:{user.user_id}:30")],
        [InlineKeyboardButton(text="➕ Свой срок", callback_data=f"admincustom:{user.user_id}")],
        [InlineKeyboardButton(text="💳 Платежи", callback_data=f"adminuserpayments:{user.user_id}"),
         InlineKeyboardButton(text="📊 Сканы", callback_data=f"adminuserscans:{user.user_id}")],
        [InlineKeyboardButton(text="⚠️ Ошибки", callback_data=f"adminusererrors:{user.user_id}")],
        [InlineKeyboardButton(text="🗑 Забрать доступ", callback_data=f"adminrevoke:{user.user_id}"),
         InlineKeyboardButton(text=ban_label, callback_data=f"adminban:{user.user_id}")],
        [InlineKeyboardButton(text="⬅️ Пользователи", callback_data="adminusers")],
    ])


def admin_user_back_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"adminuser:{user_id}")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="adminusers")],
    ])



def admin_plans_keyboard(plans: list[SubscriptionPlan]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for plan in plans:
        icon = "✅" if plan.is_active else "▫️"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {plan.title} · {plan.price_usdt:g} USDT",
            callback_data=f"adminplan:{plan.key}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="adminhome")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_plan_keyboard(plan: SubscriptionPlan) -> InlineKeyboardMarkup:
    toggle_label = "⏸ Выключить тариф" if plan.is_active else "▶️ Включить тариф"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"adminplanprice:{plan.key}")],
        [InlineKeyboardButton(text=toggle_label, callback_data=f"adminplantoggle:{plan.key}")],
        [InlineKeyboardButton(text="⬅️ Тарифы", callback_data="adminplans")],
    ])


async def render_admin_user(user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    user = await get_commerce_user(user_id)
    if user is None:
        return None
    now = datetime.utcnow()
    active = bool(user.access_until and user.access_until > now and not user.is_banned)
    async with SessionLocal() as session:
        scans = (await session.execute(select(func.count(UserScan.id)).where(UserScan.user_id == user.user_id))).scalar_one()
        paid = (await session.execute(select(func.count(SubscriptionPayment.id)).where(
            SubscriptionPayment.user_id == user.user_id, SubscriptionPayment.status == "paid"
        ))).scalar_one()
        pending = (await session.execute(select(func.count(SubscriptionPayment.id)).where(
            SubscriptionPayment.user_id == user.user_id, SubscriptionPayment.status == "pending"
        ))).scalar_one()
        errors = (await session.execute(select(func.count(ParserRun.id)).where(
            ParserRun.user_id == user.user_id, ParserRun.success.is_(False)
        ))).scalar_one()
        last_scan = (await session.execute(
            select(UserScan).where(UserScan.user_id == user.user_id).order_by(UserScan.created_at.desc()).limit(1)
        )).scalar_one_or_none()
    name = f"@{html.escape(user.username)}" if user.username else html.escape(user.first_name or "без username")
    scan_line = "—"
    if last_scan is not None:
        scan_line = f"{_date_label(last_scan.target_date)} · {last_scan.status} · {last_scan.result_count} результатов"
    text = (
        f"<b>👤 Пользователь</b>\n\n"
        f"{name}\n"
        f"ID: <code>{user.user_id}</code>\n"
        f"Статус: <b>{'⛔ заблокирован' if user.is_banned else ('✅ активен' if active else '▫️ без доступа')}</b>\n"
        f"Доступ до: <b>{_utc_to_msk_text(user.access_until)} МСК</b>\n"
        f"Первый вход: <b>{_utc_to_msk_text(user.joined_at)} МСК</b>\n"
        f"Последняя активность: <b>{_utc_to_msk_text(user.last_seen_at)} МСК</b>\n\n"
        f"📊 Сканов: <b>{int(scans or 0)}</b>\n"
        f"Последний: <b>{html.escape(scan_line)}</b>\n"
        f"⚠️ Ошибок парсера: <b>{int(errors or 0)}</b>\n"
        f"💳 Оплат: <b>{int(paid or 0)}</b> · ожидают: <b>{int(pending or 0)}</b>\n"
        f"💰 Оплачено всего: <b>{float(user.paid_total_usdt or 0):g} USDT</b>"
    )
    return text, admin_user_keyboard(user)


async def send_access_screen(message: Message, user_id: int) -> None:
    if is_banned_cached(user_id):
        await message.answer("⛔ <b>Доступ к сервису заблокирован.</b>", parse_mode=ParseMode.HTML)
        return
    await message.answer(
        await subscription_text(user_id),
        parse_mode=ParseMode.HTML,
        reply_markup=await subscription_keyboard(user_id),
    )


class ActivityAccessMiddleware(BaseMiddleware):
    """Track users and keep commercial access checks outside parser handlers."""

    async def __call__(self, handler, event, data):
        tg_user = getattr(event, "from_user", None)
        if tg_user is None:
            return await handler(event, data)
        try:
            await touch_user(tg_user)
        except Exception:
            log.exception("Could not update user activity user=%s", tg_user.id)

        uid = int(tg_user.id)
        if uid in ADMIN_IDS or allowed(uid):
            return await handler(event, data)

        # /start and subscription/payment callbacks must stay reachable without access.
        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text.startswith("/start"):
                return await handler(event, data)
            if text.startswith("/admin"):
                return await handler(event, data)
            if text.startswith("/subscription"):
                return await handler(event, data)
            if text.startswith("/help"):
                return await handler(event, data)
            await send_access_screen(event, uid)
            return None

        if isinstance(event, CallbackQuery):
            callback_data = event.data or ""
            public_prefixes = ("buyplan:", "payprovider:", "paycheck:", "mypayments")
            if callback_data == "subscription" or callback_data.startswith(public_prefixes):
                return await handler(event, data)
            if is_banned_cached(uid):
                await event.answer("Доступ заблокирован", show_alert=True)
                return None
            await event.answer("Нужна активная подписка", show_alert=True)
            if event.message:
                await send_access_screen(event.message, uid)
            return None
        return None


dp = Dispatcher()

# Commercial access/user tracking runs before regular handlers. It never performs
# parser work, so Telegram navigation stays responsive while scans run in background.
_access_middleware = ActivityAccessMiddleware()
dp.message.outer_middleware(_access_middleware)
dp.callback_query.outer_middleware(_access_middleware)


def _is_admin(user_id: int) -> bool:
    return int(user_id) in ADMIN_IDS


async def _admin_dashboard_text() -> str:
    stats = await admin_stats()
    providers = providers_status()
    traffic = await TRAFFIC.snapshot()
    return (
        "<b>🛠 Админ-панель</b>\n\n"
        f"Доступ: <b>{_access_mode_label()}</b>\n"
        f"Оплата: CryptoBot {'✅' if providers['cryptobot'] else '▫️'} · "
        f"xRocket {'✅' if providers['xrocket'] else '▫️'}\n\n"
        "<b>👥 Пользователи</b>\n"
        f"Всего: <b>{stats['total_users']}</b> · за 24ч активны: <b>{stats['active_24h']}</b>\n"
        f"Новых за 24ч: <b>{stats['new_24h']}</b> · активных подписок: <b>{stats['active_users']}</b>\n\n"
        "<b>📊 Использование</b>\n"
        f"Сканов: <b>{stats['total_scans']}</b> · за 24ч: <b>{stats['scans_24h']}</b>\n"
        f"Активные сетевые лимиты: scan <b>{traffic.scan_limit}</b> · views <b>{traffic.view_limit}</b> · "
        f"global <b>{traffic.global_limit}</b>\n\n"
        "<b>💳 Платежи</b>\n"
        f"Успешных: <b>{stats['paid_count']}</b> · ожидают: <b>{stats['pending_payments']}</b>\n"
        f"За 24ч: <b>{stats['paid_24h']:g} USDT</b> · всего: <b>{stats['paid_total']:g} USDT</b>"
    )


async def _edit_or_answer(target: Message, text: str, *, reply_markup=None) -> None:
    """Prefer editing inline-menu messages, but gracefully fall back to a new one."""
    try:
        await target.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except Exception:
        await target.answer(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


@dp.callback_query(F.data == "subscription")
async def subscription_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await _edit_or_answer(
        callback.message,
        await subscription_text(callback.from_user.id),
        reply_markup=await subscription_keyboard(callback.from_user.id),
    )


@dp.callback_query(F.data == "mypayments")
async def my_payments_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    payments = await user_payments(callback.from_user.id, 15)
    lines = ["<b>💳 Мои платежи</b>", ""]
    if not payments:
        lines.append("Платежей пока нет.")
    else:
        for p in payments[:10]:
            plan = await get_plan(p.plan_key)
            title = plan.title if plan else p.plan_key
            date_text = _utc_to_msk_text(p.paid_at or p.created_at)
            lines.append(
                f"{_payment_status_label(p.status)} · <b>{html.escape(title)}</b> · "
                f"{p.amount_usdt:g} USDT · {_provider_label(p.provider)}\n"
                f"#{p.id} · {date_text} МСК"
            )
    await callback.answer()
    await _edit_or_answer(callback.message, "\n\n".join(lines), reply_markup=user_payments_keyboard(payments))


@dp.callback_query(F.data.startswith("buyplan:"))
async def buy_plan_handler(callback: CallbackQuery) -> None:
    if is_banned_cached(callback.from_user.id):
        await callback.answer("Доступ заблокирован", show_alert=True)
        return
    if current_access_mode() != "subscription":
        await callback.answer("Продажа подписок сейчас выключена", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    plan = await get_plan(key)
    if plan is None or not plan.is_active:
        await callback.answer("Этот тариф сейчас недоступен", show_alert=True)
        return
    providers = providers_status()
    if not any(providers.values()):
        await callback.answer("Оплата пока не настроена", show_alert=True)
        return
    await callback.answer()
    text = (
        "<b>💎 Оформление подписки</b>\n\n"
        f"Тариф: <b>{html.escape(plan.title)}</b>\n"
        f"Срок: <b>{plan.days} дн.</b>\n"
        f"Стоимость: <b>{plan.price_usdt:g} USDT</b>\n\n"
        "Выбери способ оплаты. Сумма и срок задаются ботом автоматически."
    )
    await _edit_or_answer(callback.message, text, reply_markup=payment_provider_keyboard(plan.key))


@dp.callback_query(F.data.startswith("payprovider:"))
async def create_payment_handler(callback: CallbackQuery) -> None:
    if is_banned_cached(callback.from_user.id):
        await callback.answer("Доступ заблокирован", show_alert=True)
        return
    try:
        _, provider, plan_key = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("Некорректный способ оплаты", show_alert=True)
        return
    await callback.answer("Создаю счёт…")
    try:
        payment = await create_subscription_payment(callback.from_user.id, plan_key, provider)
    except PaymentProviderError as exc:
        await callback.message.answer(
            f"⚠️ <b>Не удалось создать счёт</b>\n{html.escape(str(exc))}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Подписка", callback_data="subscription")]
            ]),
        )
        return
    except Exception:
        log.exception("Could not create payment invoice")
        await callback.message.answer("⚠️ Не удалось создать счёт. Попробуй чуть позже.")
        return

    plan = await get_plan(payment.plan_key)
    title = plan.title if plan else payment.plan_key
    text = (
        "<b>💳 Счёт создан</b>\n\n"
        f"Способ: <b>{_provider_label(payment.provider)}</b>\n"
        f"Тариф: <b>{html.escape(title)}</b>\n"
        f"Сумма: <b>{payment.amount_usdt:g} USDT</b>\n"
        f"Действует до: <b>{_utc_to_msk_text(payment.expires_at)} МСК</b>\n\n"
        "Нажми «Открыть оплату». После оплаты бот проверит счёт автоматически; "
        "кнопка «Проверить оплату» нужна только если хочешь проверить сразу."
    )
    await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=payment_invoice_keyboard(payment))


@dp.callback_query(F.data.startswith("paycheck:"))
async def check_payment_handler(callback: CallbackQuery) -> None:
    try:
        payment_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный счёт", show_alert=True)
        return
    payment = await get_payment(payment_id)
    if payment is None or (payment.user_id != callback.from_user.id and not _is_admin(callback.from_user.id)):
        await callback.answer("Счёт не найден", show_alert=True)
        return
    await callback.answer("Проверяю…")
    refreshed, just_activated = await refresh_payment(payment_id)
    if refreshed is None:
        await callback.message.answer("⚠️ Счёт не найден.")
        return
    if refreshed.status == "paid":
        user = await get_commerce_user(refreshed.user_id)
        text = (
            "✅ <b>Оплата подтверждена</b>\n\n"
            f"Подписка активна до <b>{_utc_to_msk_text(user.access_until if user else None)} МСК</b>."
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Перейти в сервис", callback_data="home")],
            [InlineKeyboardButton(text="💎 Подписка", callback_data="subscription")],
        ])
        await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    if refreshed.status in {"expired", "failed", "cancelled"}:
        title = "⌛ <b>Срок счёта истёк.</b>" if refreshed.status == "expired" else "❌ <b>Этот счёт нельзя подтвердить.</b>"
        await callback.message.answer(
            title + " Создай новый счёт — старый повторно использовать не нужно.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Создать новый счёт", callback_data=f"buyplan:{refreshed.plan_key}")],
                [InlineKeyboardButton(text="💳 Мои платежи", callback_data="mypayments")],
                [InlineKeyboardButton(text="💎 Подписка", callback_data="subscription")]
            ]),
        )
        return
    await callback.message.answer(
        "⏳ <b>Оплата пока не подтверждена.</b>\nЕсли ты только что оплатил, подожди несколько секунд — бот проверяет счета автоматически.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Проверить ещё раз", callback_data=f"paycheck:{refreshed.id}")],
            [InlineKeyboardButton(text="💳 Мои платежи", callback_data="mypayments")],
        ]),
    )


@dp.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not _is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer(await _admin_dashboard_text(), parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())


@dp.callback_query(F.data == "adminhome")
async def admin_home_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _edit_or_answer(callback.message, await _admin_dashboard_text(), reply_markup=admin_keyboard())


@dp.callback_query(F.data == "adminstats")
async def admin_stats_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _edit_or_answer(callback.message, await _admin_dashboard_text(), reply_markup=admin_keyboard())


@dp.callback_query(F.data == "adminusers")
async def admin_users_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    users = await recent_users(20)
    await callback.answer()
    text = "<b>👥 Пользователи</b>\n\nПоследние по активности. Нажми на пользователя для управления доступом."
    if not users:
        text += "\n\nПока никого нет."
    await _edit_or_answer(callback.message, text, reply_markup=admin_users_keyboard(users))


@dp.callback_query(F.data == "adminusersearch")
async def admin_user_search_begin(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminInput.user_search)
    await callback.answer()
    await callback.message.answer(
        "🔎 Отправь <b>Telegram ID</b>, <b>@username</b> или имя пользователя.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(),
    )


@dp.message(AdminInput.user_search)
async def admin_user_search_message(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    query = (message.text or "").strip()
    users = await find_users(query, 20)
    await state.clear()
    text = f"<b>🔎 Результаты поиска</b>\n\nЗапрос: <code>{html.escape(query)}</code>"
    if not users:
        text += "\n\nНичего не найдено. Пользователь должен хотя бы раз открыть бота."
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_users_keyboard(users))


@dp.callback_query(F.data.startswith("adminuser:"))
async def admin_user_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        uid = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    rendered = await render_admin_user(uid)
    if rendered is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.answer()
    await _edit_or_answer(callback.message, rendered[0], reply_markup=rendered[1])


@dp.callback_query(F.data.startswith("admincustom:"))
async def admin_custom_days_begin(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        uid = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await state.set_state(AdminInput.custom_days)
    await state.update_data(admin_target_user=uid)
    await callback.answer()
    await callback.message.answer(
        f"➕ Сколько дней добавить пользователю <code>{uid}</code>?\n\nОтправь число от <b>1</b> до <b>3650</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_user_back_keyboard(uid),
    )


@dp.message(AdminInput.custom_days)
async def admin_custom_days_message(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    uid = int(data.get("admin_target_user") or 0)
    try:
        days = int((message.text or "").strip())
        if days < 1 or days > 3650:
            raise ValueError
    except Exception:
        await message.answer("⚠️ Отправь целое число от 1 до 3650.")
        return
    until = await grant_access_days(uid, days)
    await state.clear()
    try:
        await message.bot.send_message(
            uid,
            f"✅ <b>Доступ продлён на {days} дн.</b>\nАктивен до <b>{_utc_to_msk_text(until)} МСК</b>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    rendered = await render_admin_user(uid)
    if rendered:
        await message.answer(rendered[0], parse_mode=ParseMode.HTML, reply_markup=rendered[1])


@dp.callback_query(F.data.startswith("adminuserpayments:"))
async def admin_user_payments_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    uid = int(callback.data.split(":", 1)[1])
    payments = await user_payments(uid, 20)
    lines = [f"<b>💳 Платежи пользователя</b>", f"ID: <code>{uid}</code>", ""]
    if not payments:
        lines.append("Платежей нет.")
    else:
        for p in payments[:15]:
            plan = await get_plan(p.plan_key)
            title = plan.title if plan else p.plan_key
            when = _utc_to_msk_text(p.paid_at or p.created_at)
            lines.append(
                f"{_payment_status_label(p.status)} · <b>{html.escape(title)}</b> · "
                f"{p.amount_usdt:g} USDT · {_provider_label(p.provider)} · {when}"
            )
    await callback.answer()
    await _edit_or_answer(callback.message, "\n".join(lines), reply_markup=admin_user_back_keyboard(uid))


@dp.callback_query(F.data.startswith("adminuserscans:"))
async def admin_user_scans_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    uid = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        scans = list((await session.execute(
            select(UserScan).where(UserScan.user_id == uid).order_by(UserScan.created_at.desc()).limit(12)
        )).scalars().all())
    lines = ["<b>📊 Последние сканы пользователя</b>", f"ID: <code>{uid}</code>", ""]
    if not scans:
        lines.append("Сканов нет.")
    else:
        for scan in scans:
            icon = {"done": "✅", "partial": "⚠️", "failed": "❌", "cancelled": "⏹", "running": "🔄", "queued": "⏳"}.get(scan.status, "▫️")
            recovered = f" · ♻️{scan.resumed_count}" if int(scan.resumed_count or 0) else ""
            lines.append(
                f"{icon} <b>#{scan.id}</b> · {_date_label(scan.target_date)} · {html.escape(scan.title[:45])}\n"
                f"результат: {scan.result_count} · качество: {scan.quality_score}/100{recovered}"
            )
    await callback.answer()
    await _edit_or_answer(callback.message, "\n\n".join(lines), reply_markup=admin_user_back_keyboard(uid))


@dp.callback_query(F.data.startswith("adminusererrors:"))
async def admin_user_errors_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    uid = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        runs = list((await session.execute(
            select(ParserRun)
            .where(ParserRun.user_id == uid, ParserRun.success.is_(False))
            .order_by(ParserRun.started_at.desc())
            .limit(8)
        )).scalars().all())
        scan_errors = list((await session.execute(
            select(UserScan)
            .where(UserScan.user_id == uid, UserScan.last_error.is_not(None))
            .order_by(UserScan.created_at.desc())
            .limit(5)
        )).scalars().all())
    lines = ["<b>⚠️ Последние ошибки</b>", f"ID: <code>{uid}</code>", ""]
    if not runs and not scan_errors:
        lines.append("Ошибок не зафиксировано.")
    for scan in scan_errors:
        lines.append(f"Скан #{scan.id}: <code>{html.escape((scan.last_error or '')[:220])}</code>")
    for run in runs:
        lines.append(
            f"{_utc_to_msk_text(run.started_at)} · {html.escape(run.category_name[:40])}\n"
            f"<code>{html.escape((run.error_text or run.stop_reason or 'ошибка')[:220])}</code>"
        )
    await callback.answer()
    await _edit_or_answer(callback.message, "\n\n".join(lines), reply_markup=admin_user_back_keyboard(uid))


@dp.callback_query(F.data.startswith("admingrant:"))
async def admin_grant_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        _, uid_raw, days_raw = callback.data.split(":", 2)
        uid, days = int(uid_raw), int(days_raw)
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    until = await grant_access_days(uid, days)
    await callback.answer(f"Добавлено {days} дн.")
    try:
        await callback.bot.send_message(
            uid,
            f"✅ <b>Доступ продлён на {days} дн.</b>\nАктивен до <b>{_utc_to_msk_text(until)} МСК</b>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    rendered = await render_admin_user(uid)
    if rendered:
        await _edit_or_answer(callback.message, rendered[0], reply_markup=rendered[1])


@dp.callback_query(F.data.startswith("adminrevoke:"))
async def admin_revoke_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        uid = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await revoke_access(uid)
    await callback.answer("Доступ отозван")
    rendered = await render_admin_user(uid)
    if rendered:
        await _edit_or_answer(callback.message, rendered[0], reply_markup=rendered[1])


@dp.callback_query(F.data.startswith("adminban:"))
async def admin_ban_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        uid = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    user = await get_commerce_user(uid)
    new_value = not bool(user and user.is_banned)
    await set_banned(uid, new_value)
    await callback.answer("Заблокирован" if new_value else "Разблокирован")
    rendered = await render_admin_user(uid)
    if rendered:
        await _edit_or_answer(callback.message, rendered[0], reply_markup=rendered[1])


@dp.callback_query(F.data == "adminpayments")
async def admin_payments_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    payments = await recent_payments(20)
    providers = providers_status()
    lines = [
        "<b>💳 Платежи</b>",
        "",
        f"CryptoBot: <b>{'✅ настроен' if providers['cryptobot'] else '▫️ нет токена'}</b>",
        f"xRocket: <b>{'✅ настроен' if providers['xrocket'] else '▫️ нет API key'}</b>",
    ]
    if payments:
        lines.extend(["", "<b>Последние счета</b>"])
        for p in payments[:15]:
            user = await get_commerce_user(p.user_id)
            who = f"@{user.username}" if user and user.username else str(p.user_id)
            lines.append(
                f"{_payment_status_label(p.status)} · <b>{p.amount_usdt:g} USDT</b> · "
                f"{html.escape(who)} · {_provider_label(p.provider)} · {_utc_to_msk_text(p.created_at)}"
            )
    else:
        lines.extend(["", "Платежей пока нет."])
    await callback.answer()
    await _edit_or_answer(callback.message, "\n".join(lines), reply_markup=admin_back_keyboard())


@dp.callback_query(F.data == "adminplans")
async def admin_plans_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    plans = await get_plans()
    await callback.answer()
    await _edit_or_answer(
        callback.message,
        "<b>🎟 Тарифы</b>\n\nЦена меняется здесь и сразу применяется к новым счетам.",
        reply_markup=admin_plans_keyboard(plans),
    )


@dp.callback_query(F.data.startswith("adminplan:"))
async def admin_plan_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    plan = await get_plan(key)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    text = (
        f"<b>🎟 {html.escape(plan.title)}</b>\n\n"
        f"Срок: <b>{plan.days} дн.</b>\n"
        f"Цена: <b>{plan.price_usdt:g} USDT</b>\n"
        f"Статус: <b>{'✅ включён' if plan.is_active else '⏸ выключен'}</b>"
    )
    await callback.answer()
    await _edit_or_answer(callback.message, text, reply_markup=admin_plan_keyboard(plan))


@dp.callback_query(F.data.startswith("adminplanprice:"))
async def admin_plan_price_begin(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    plan = await get_plan(key)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    await state.set_state(AdminInput.plan_price)
    await state.update_data(admin_plan_key=key)
    await callback.answer()
    await callback.message.answer(
        f"💰 Новая цена для <b>{html.escape(plan.title)}</b> в USDT.\nНапример: <code>9.99</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(),
    )


@dp.message(AdminInput.plan_price)
async def admin_plan_price_message(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    key = data.get("admin_plan_key")
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = float(raw)
        plan = await update_plan_price(str(key), price)
    except Exception:
        await message.answer("⚠️ Нужна положительная цена, например <code>9.99</code>.", parse_mode=ParseMode.HTML)
        return
    await state.clear()
    if not plan:
        await message.answer("Тариф не найден.", reply_markup=admin_back_keyboard())
        return
    await message.answer(
        f"✅ Цена <b>{html.escape(plan.title)}</b> изменена на <b>{plan.price_usdt:g} USDT</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_plan_keyboard(plan),
    )


@dp.callback_query(F.data.startswith("adminplantoggle:"))
async def admin_plan_toggle_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    plan = await toggle_plan(key)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    await callback.answer("Включён" if plan.is_active else "Выключен")
    text = (
        f"<b>🎟 {html.escape(plan.title)}</b>\n\n"
        f"Срок: <b>{plan.days} дн.</b>\n"
        f"Цена: <b>{plan.price_usdt:g} USDT</b>\n"
        f"Статус: <b>{'✅ включён' if plan.is_active else '⏸ выключен'}</b>"
    )
    await _edit_or_answer(callback.message, text, reply_markup=admin_plan_keyboard(plan))


@dp.callback_query(F.data == "adminmode")
async def admin_mode_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    text = (
        "<b>🔐 Режим доступа</b>\n\n"
        "🔒 <b>Только админы</b> — безопасный режим для тестов.\n"
        "💎 <b>По подписке</b> — без активной подписки доступна только оплата.\n"
        "🌍 <b>Открытый доступ</b> — бот доступен всем без оплаты.\n\n"
        f"Сейчас: <b>{_access_mode_label()}</b>"
    )
    await callback.answer()
    await _edit_or_answer(callback.message, text, reply_markup=admin_mode_keyboard())


@dp.callback_query(F.data.startswith("adminsetmode:"))
async def admin_set_mode_handler(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mode = callback.data.split(":", 1)[1]
    try:
        await set_access_mode(mode)
    except ValueError:
        await callback.answer("Некорректный режим", show_alert=True)
        return
    await callback.answer("Режим изменён")
    await _edit_or_answer(
        callback.message,
        f"<b>🔐 Режим доступа</b>\n\nСейчас: <b>{_access_mode_label(mode)}</b>",
        reply_markup=admin_mode_keyboard(),
    )


async def payment_scheduler(bot: Bot) -> None:
    """Poll pending invoices. No webhook service is required for the first commercial build."""
    while True:
        try:
            payments = await pending_payments(100)
            for payment in payments:
                try:
                    refreshed, just_paid = await refresh_payment(payment.id)
                    if not refreshed or not just_paid:
                        continue
                    user = await get_commerce_user(refreshed.user_id)
                    plan = await get_plan(refreshed.plan_key)
                    until = user.access_until if user else None
                    try:
                        await bot.send_message(
                            refreshed.user_id,
                            "✅ <b>Оплата подтверждена</b>\n\n"
                            f"{html.escape(plan.title) if plan else 'Подписка'} активна до "
                            f"<b>{_utc_to_msk_text(until)} МСК</b>.\nТеперь сервис доступен.",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🏠 Открыть сервис", callback_data="home")]
                            ]),
                        )
                    except Exception:
                        log.exception("Could not notify paid user %s", refreshed.user_id)
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                "💳 <b>Новая оплата</b>\n"
                                f"User: <code>{refreshed.user_id}</code>\n"
                                f"Тариф: <b>{html.escape(plan.title) if plan else refreshed.plan_key}</b>\n"
                                f"Сумма: <b>{refreshed.amount_usdt:g} USDT</b>\n"
                                f"Способ: <b>{_provider_label(refreshed.provider)}</b>",
                                parse_mode=ParseMode.HTML,
                            )
                        except Exception:
                            pass
                except Exception:
                    log.exception("Payment polling failed for payment=%s", payment.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Payment scheduler loop failed")
        await asyncio.sleep(PAYMENT_POLL_SECONDS)


async def subscription_lifecycle_scheduler(bot: Bot) -> None:
    """Notify users once 24h before expiry and once right after expiry."""
    while True:
        try:
            warnings, expired = await subscription_notice_candidates(100)
            for user in warnings:
                until = user.access_until
                if until is None:
                    continue
                try:
                    await bot.send_message(
                        user.user_id,
                        "⏳ <b>Подписка закончится меньше чем через 24 часа.</b>\n\n"
                        f"Доступ до <b>{_utc_to_msk_text(until)} МСК</b>. Продлить можно заранее — дни прибавятся к текущему сроку.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="subscription")]
                        ]),
                    )
                    await mark_subscription_notice(user.user_id, until, "warning")
                except Exception:
                    log.debug("Could not send subscription warning user=%s", user.user_id, exc_info=True)

            for user in expired:
                until = user.access_until
                if until is None:
                    continue
                try:
                    await bot.send_message(
                        user.user_id,
                        "⌛ <b>Подписка закончилась.</b>\n\n"
                        "Сохранённые сканы и история не удалены. Продли доступ, чтобы снова запускать парсер и обновлять данные.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💎 Продлить доступ", callback_data="subscription")]
                        ]),
                    )
                    await mark_subscription_notice(user.user_id, until, "expired")
                except Exception:
                    log.debug("Could not send subscription expiry user=%s", user.user_id, exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Subscription lifecycle scheduler failed")
        await asyncio.sleep(SUBSCRIPTION_NOTICE_POLL_SECONDS)





async def setup_bot_commands(bot: Bot) -> None:
    """Configure Telegram's bottom-left Menu button and command list."""
    user_commands = [
        BotCommand(command="menu", description="🏠 Главное меню"),
        BotCommand(command="new_scan", description="▶️ Новый скан"),
        BotCommand(command="stop", description="⏹ Остановить парсер"),
        BotCommand(command="my_scans", description="📊 Мои сканы"),
        BotCommand(command="popular", description="🔥 Популярное"),
        BotCommand(command="categories", description="🗂 Категории"),
        BotCommand(command="settings", description="⚙️ Настройки"),
        BotCommand(command="subscription", description="💎 Подписка"),
        BotCommand(command="help", description="ℹ️ Помощь"),
    ]
    admin_commands = user_commands + [
        BotCommand(command="admin", description="🛠 Админ-панель"),
    ]

    # Default command menu for every private user.
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    # Admin chats get the same menu plus /admin.
    for admin_id in sorted(ADMIN_IDS):
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            log.exception("Could not set admin command menu for chat=%s", admin_id)

    # Force Telegram to render the standard Commands menu button instead of
    # requiring users to type slash commands manually.
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def onboarding_keyboard(step: int) -> InlineKeyboardMarkup:
    if step == 1:
        rows = [
            [InlineKeyboardButton(text="➡️ Как пользоваться", callback_data="onboard:2")],
            [InlineKeyboardButton(text="Пропустить", callback_data="onboard:skip")],
        ]
    elif step == 2:
        rows = [
            [InlineKeyboardButton(text="➡️ Что будет после скана", callback_data="onboard:3")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="onboard:1")],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text="🗂 Выбрать категории", callback_data="onboard:categories")],
            [InlineKeyboardButton(text="🏠 Открыть главное меню", callback_data="onboard:finish")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="onboard:2")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def onboarding_text(step: int) -> str:
    if step == 1:
        return (
            f"<b>👋 Kleinanzeigen Analytics</b>\n\n"
            "Бот помогает найти товары, которые реально привлекают внимание на Kleinanzeigen, "
            "и затем автоматически измеряет рост просмотров.\n\n"
            "Настройка первого запуска занимает меньше минуты."
        )
    if step == 2:
        return (
            "<b>1/2 · Как запустить первый скан</b>\n\n"
            "1. 🗂 Выбери одну или несколько категорий.\n"
            "2. ⚙️ При необходимости задай цену, уникальность и слова-фильтры.\n"
            "3. ▶️ Нажми «Новый скан», выбери дату и глубину 25 / 50 / 100 страниц.\n\n"
            "Во время работы парсер можно полностью остановить кнопкой ⏹."
        )
    return (
        "<b>2/2 · Что будет после скана</b>\n\n"
        "📊 Скан сохранится в «Мои сканы».\n"
        "👁 Бот сделает автозамеры через 3 / 6 / 12 часов.\n"
        "🔥 В «Популярное» появятся лидеры по просмотрам и росту.\n"
        "📦 Через 24 часа карточка уйдёт в Архив, но данные не удалятся.\n\n"
        "Готово — можно выбирать категории."
    )


async def _show_onboarding(message: Message, user_id: int, step: int = 1) -> None:
    step = max(1, min(3, int(step)))
    await message.answer(
        onboarding_text(step),
        parse_mode=ParseMode.HTML,
        reply_markup=onboarding_keyboard(step),
    )


def home_text(selected_count: int) -> str:
    if selected_count:
        state_line = f"🗂 Категории: <b>{selected_count}/{MAX_SELECTED_CATEGORIES}</b> · можно запускать скан"
    else:
        state_line = "🗂 Категории: <b>не выбраны</b>"
    return (
        "<b>🔎 Kleinanzeigen Analytics</b>\n\n"
        "Находи объявления, которые быстрее остальных набирают просмотры.\n\n"
        f"{state_line}\n\n"
        "Выбери действие ниже."
    )


async def _send_home_message(message: Message, user_id: int, *, intro: bool = False) -> None:
    """Send the branded DT PARSER home card with navigation buttons below it."""
    selected = await get_selected(user_id)
    markup = main_keyboard(len(selected), admin=_is_admin(user_id))
    caption = home_text(len(selected))

    if MENU_IMAGE_PATH.exists():
        await message.answer_photo(
            photo=FSInputFile(MENU_IMAGE_PATH),
            caption=caption,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        return

    # Safe fallback if the asset was accidentally omitted from a deployment.
    log.error("Main menu image is missing: %s", MENU_IMAGE_PATH)
    await message.answer(caption, reply_markup=markup, parse_mode=ParseMode.HTML)


async def _send_popular_message(message: Message, user_id: int) -> None:
    items = await get_user_popular_categories(user_id)
    if not items:
        text = (
            "🔥 <b>Популярное</b>\n\n"
            "После первого успешного скана здесь появится актуальный TOP по категории."
        )
    else:
        text = (
            "🔥 <b>Популярное</b>\n\n"
            "Выбери категорию — показываем только её <b>последний успешный скан</b>.\n"
            "TOP роста доступен по замерам 3 / 6 / 12 часов."
        )
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=popular_categories_keyboard(items),
        disable_web_page_preview=True,
    )


async def _send_my_scans_message(message: Message, user_id: int) -> None:
    scans = await get_user_scans(user_id, 10)
    archive_count = await get_archive_count(user_id)
    if not scans:
        text = (
            "<b>📊 Мои сканы</b>\n\nСвежих сканов пока нет. После завершения они будут храниться здесь 24 часа, затем уйдут в Архив."
        )
    else:
        text = (
            "<b>📊 Мои сканы</b>\n\nТекущие и свежие запуски за последние <b>24 часа</b>. Старые автоматически уходят в Архив; данные не удаляются."
        )
    await message.answer(
        text, parse_mode=ParseMode.HTML,
        reply_markup=my_scans_keyboard(scans, archive_count),
    )


async def _begin_scan_from_message(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    selected_keys = await get_selected(user_id)
    selected_cats = [CATEGORIES[k] for k in CATEGORIES if k in selected_keys]
    if not selected_cats:
        await message.answer(
            "⚠️ <b>Сначала выбери хотя бы одну категорию.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=groups_keyboard(selected_keys),
        )
        return

    async with job_guard:
        existing = active_jobs.get(user_id)
        if existing and existing.state in {"queued", "running"} and not existing.cancel_requested:
            await message.answer("⏳ У тебя уже идёт парсинг.")
            return

    await state.set_state(ScanInput.target_date)
    await message.answer(
        "<b>▶️ Новый скан</b>\n\n"
        "<b>1/2 · Дата объявлений</b>\n"
        "Выбери день. Время считаем по Москве.",
        parse_mode=ParseMode.HTML,
        reply_markup=scan_date_keyboard(),
    )


@dp.callback_query(F.data.startswith("onboard:"))
async def onboarding_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    if action in {"1", "2", "3"}:
        await callback.answer()
        await _edit_or_answer(callback.message, onboarding_text(int(action)), reply_markup=onboarding_keyboard(int(action)))
        return
    if action in {"finish", "skip"}:
        await set_onboarding_completed(callback.from_user.id, True)
        await callback.answer("Готово")
        await _send_home_message(callback.message, callback.from_user.id)
        return
    if action == "categories":
        await set_onboarding_completed(callback.from_user.id, True)
        selected = await get_selected(callback.from_user.id)
        await callback.answer()
        await _edit_or_answer(
            callback.message,
            "<b>🗂 Выбери категории</b>\n\nОтметь товары, которые хочешь анализировать. Потом вернись и запускай первый скан.",
            reply_markup=groups_keyboard(selected),
        )
        return
    await callback.answer("Неизвестное действие", show_alert=True)


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        await touch_user(message.from_user, force=True)
    except Exception:
        # Never leave a new user with a silent /start if PostgreSQL/profile storage
        # is temporarily unavailable. The outer middleware already logs its own
        # attempt, but this handler used to raise before sending any Telegram reply.
        log.exception("Could not persist /start user=%s", message.from_user.id)
        await message.answer(
            "⚠️ <b>Не удалось зарегистрировать профиль</b>\n\n"
            "База данных временно не приняла запрос. Попробуй нажать /start ещё раз через несколько секунд.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not allowed(message.from_user.id):
        await send_access_screen(message, message.from_user.id)
        return
    user = await get_commerce_user(message.from_user.id)
    if user is not None and not bool(user.onboarding_completed):
        await _show_onboarding(message, message.from_user.id, 1)
        return
    await _send_home_message(message, message.from_user.id, intro=True)


@dp.message(Command("menu"))
async def menu_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_home_message(message, message.from_user.id)


@dp.message(Command("new_scan"))
async def new_scan_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _begin_scan_from_message(message, state)


@dp.message(Command("my_scans"))
async def my_scans_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_my_scans_message(message, message.from_user.id)


@dp.message(Command("popular"))
async def popular_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_popular_message(message, message.from_user.id)


@dp.message(Command("categories"))
async def categories_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    selected = await get_selected(message.from_user.id)
    await message.answer(
        f"<b>🗂 Категории</b>\n\nВыбери до <b>{MAX_SELECTED_CATEGORIES}</b> категорий на один скан.",
        reply_markup=groups_keyboard(selected),
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command("settings"))
async def settings_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    s = await get_settings(message.from_user.id)
    await message.answer(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.message(Command("subscription"))
async def subscription_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        await subscription_text(message.from_user.id),
        parse_mode=ParseMode.HTML,
        reply_markup=await subscription_keyboard(message.from_user.id),
    )


@dp.message(Command("result"))
async def result_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    selected = await get_selected(message.from_user.id)
    await send_smart_export(message, message.from_user.id, len(selected))


@dp.message(Command("help"))
async def help_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    selected = await get_selected(message.from_user.id)
    await message.answer(
        "<b>ℹ️ Помощь</b>\n\n"
        "Основные разделы всегда доступны через кнопку <b>Menu</b>.\n\n"
        "▶️ Новый скан — выбрать дату и запустить сбор\n"
        "🔥 Популярное — актуальный TOP последнего успешного скана\n"
        "📊 Мои сканы — свежие запуски и архив\n"
        "🗂 Категории — что анализировать\n"
        "⚙️ Настройки — фильтры результата\n"
        "💎 Подписка — доступ и платежи",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎓 Показать обучение", callback_data="onboard:1")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="home")],
        ]),
    )


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    user = await get_commerce_user(callback.from_user.id)
    await callback.answer()
    if user is not None and not bool(user.onboarding_completed):
        await _edit_or_answer(callback.message, onboarding_text(1), reply_markup=onboarding_keyboard(1))
        return
    await _send_home_message(callback.message, callback.from_user.id)


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
    await callback.answer()
    await _send_home_message(callback.message, callback.from_user.id)


@dp.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    s = await get_settings(callback.from_user.id)
    await callback.answer()
    await _edit_or_answer(callback.message, settings_text(s), reply_markup=settings_keyboard(s))


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
    # Compatibility for old Telegram messages created before v3.2.7.
    s = await get_settings(callback.from_user.id)
    await callback.answer("Период перенесён в запуск парсера")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("period:"))
async def choose_period(callback: CallbackQuery) -> None:
    # Do not preserve a hidden legacy period: date is now selected only at scan start.
    s = await get_settings(callback.from_user.id)
    await callback.answer("Эта настройка больше не используется")
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


@dp.callback_query(F.data == "set_min_views")
async def set_min_views(callback: CallbackQuery) -> None:
    s = await get_settings(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(
        "<b>👁 Минимум просмотров</b>\n\n"
        "В результат попадут только объявления, у которых текущее число просмотров не ниже выбранного порога.\n\n"
        "<i>Важно: бот всё равно должен сначала получить счётчик просмотров у объявления, поэтому эта настройка фильтрует результат, а не уменьшает число запросов к Kleinanzeigen.</i>",
        reply_markup=min_views_keyboard(int(getattr(s, "min_views", 0) or 0)),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("minviews:"))
async def choose_min_views(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await state.set_state(SettingsInput.min_views)
        await callback.answer()
        await callback.message.answer(
            "👁 Пришли минимальное количество просмотров числом.\n\n"
            "Например: <code>75</code>\n"
            "Чтобы отключить порог — отправь <code>0</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        threshold = max(0, min(10_000_000, int(value)))
    except ValueError:
        await callback.answer("Некорректное значение", show_alert=True)
        return
    s = await update_setting(callback.from_user.id, "min_views", threshold)
    await callback.answer("Порог просмотров сохранён")
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s), parse_mode=ParseMode.HTML)


@dp.message(SettingsInput.min_views)
async def save_custom_min_views(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    try:
        threshold = int(raw)
    except ValueError:
        await message.answer("Пришли целое число, например <code>75</code>.", parse_mode=ParseMode.HTML)
        return
    if threshold < 0 or threshold > 10_000_000:
        await message.answer("Укажи значение от 0 до 10 000 000.")
        return
    s = await update_setting(message.from_user.id, "min_views", threshold)
    await state.clear()
    await message.answer(
        "✅ Порог просмотров сохранён.\n\n" + settings_text(s),
        reply_markup=settings_keyboard(s),
        parse_mode=ParseMode.HTML,
    )


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
            "✅ Массовый сбор v3.1.6 использует только быстрый direct-счётчик. Chromium оставлен только для этого точечного теста/диагностики.",
        ]
    else:
        lines += [
            "",
            "ℹ️ В массовом сборе browser fallback отключён: проблемные объявления будут отмечены как «без данных», чтобы не перегружать сервис.",
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
            "🔥 <b>Популярное</b>\n\n"
            "После первого успешного скана здесь появится актуальный TOP по категории."
        )
    else:
        text = (
            "🔥 <b>Популярное</b>\n\n"
            "Выбери категорию — показываем только её <b>последний успешный скан</b>.\n"
            "TOP роста доступен по замерам 3 / 6 / 12 часов."
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
    rows, scans = await get_category_scan_rows(callback.from_user.id, category_key)
    if cat is None or not scans:
        await callback.answer("Категория или сканы не найдены", show_alert=True); return
    scan_settings = await get_settings(callback.from_user.id)
    rows = apply_listing_settings(rows, scan_settings, exact_date_scan=True, apply_output_mode=True)
    scan = scans[0]
    viewed = sum(1 for row in rows if row.view_count is not None)
    await callback.answer()
    finished_label = _moscow_text(scan.finished_at or scan.created_at)
    text = (
        f"🔥 <b>{html.escape(cat.name)}</b>\n\n"
        f"📅 <b>{html.escape(_date_label(scan.target_date))}</b>\n"
        f"🕒 Последний скан: <b>{html.escape(finished_label)}</b>\n"
        f"📦 Объявлений: <b>{len(rows)}</b> · 👁 С просмотрами: <b>{viewed}</b>\n\n"
        "Актуальный TOP по последнему успешному скану."
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
    try:
        representative_scan_id, category_key = int(parts[1]), parts[2]
    except Exception:
        await callback.answer("Некорректный запрос", show_alert=True); return
    cat = CATEGORIES.get(category_key)
    rows, scans = await get_category_scan_rows(callback.from_user.id, category_key)
    if cat is None or not scans:
        await callback.answer("Сканы категории не найдены", show_alert=True); return
    # Prefer the newest owned scan for navigation even when an old Telegram
    # message contains a stale representative scan id.
    scan_id = scans[0].id
    scan_settings = await get_settings(callback.from_user.id)
    rows = apply_listing_settings(rows, scan_settings, exact_date_scan=True, apply_output_mode=True)
    rows = [row for row in rows if row.view_count is not None]
    rows.sort(key=lambda row: (row.view_count or 0, row.first_seen_at), reverse=True)
    await callback.answer()
    if not rows:
        text = f"👁 <b>{html.escape(cat.name)}</b>\n\nВ последнем успешном скане пока нет объявлений с подходящими данными просмотров."
    else:
        lines = [
            f"👁 <b>Самые просматриваемые · {html.escape(cat.name)}</b>",
            f"Последний успешный скан · дата объявлений: <b>{html.escape(_date_label(scans[0].target_date))}</b>",
            "",
        ]
        for i, row in enumerate(rows[:GROWTH_TELEGRAM_LIMIT], 1):
            lines.append(
                f"<b>{i}. {html.escape(row.title[:60])}</b>\n"
                f"📅 {_date_label(row.posted_date_msk)} · 👁 <b>{row.view_count}</b> · "
                f"💶 {html.escape(_price_display(row.price_text, row.price_eur))}\n"
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
        representative_scan_id, category_key, period_hours = int(parts[1]), parts[2], int(parts[3])
    except Exception:
        await callback.answer("Некорректный запрос", show_alert=True); return
    if period_hours not in OBSERVATION_HOURS:
        period_hours = 3
    scans = await get_user_category_scans(callback.from_user.id, category_key)
    cat = CATEGORIES.get(category_key)
    if not scans or cat is None:
        await callback.answer("Сканы категории не найдены", show_alert=True); return
    scan_id = scans[0].id
    growth, scan_count, rounds = await get_category_growth_rows(
        callback.from_user.id, category_key, period_hours
    )
    scan_settings = await get_settings(callback.from_user.id)
    allowed_growth_rows = apply_listing_settings(
        [item.listing for item in growth], scan_settings, exact_date_scan=True, apply_output_mode=True
    )
    allowed_growth_ids = {row.external_id for row in allowed_growth_rows}
    growth = [item for item in growth if item.listing.external_id in allowed_growth_ids]
    await callback.answer()
    period_label = f"{period_hours} ч"
    if not growth:
        text = (
            f"🚀 <b>TOP роста · {html.escape(cat.name)} · {period_label}</b>\n\n"
            "Проверен последний успешный скан категории. "
            "Контрольные замеры для этого периода ещё не готовы или прироста пока нет. "
            "Автоматические замеры выполняются через 3 / 6 / 12 часов после каждого скана."
        )
    else:
        lines = [
            f"🚀 <b>TOP роста · {html.escape(cat.name)} · {period_label}</b>",
            "Последний успешный скан категории.",
            "Сортировка: <b>кто набрал больше всего новых просмотров</b>.",
            "",
        ]
        for i, item in enumerate(growth[:GROWTH_TELEGRAM_LIMIT], 1):
            row = item.listing
            lines.append(
                f"<b>{i}. {html.escape(row.title[:60])}</b>\n"
                f"📅 {_date_label(row.posted_date_msk)} · 👁 {item.base_views} → <b>{item.current_views}</b> · "
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
async def my_scans(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    scans = await get_user_scans(callback.from_user.id, 10)
    archive_count = await get_archive_count(callback.from_user.id)
    await callback.answer()
    if not scans:
        text = (
            "<b>📊 Мои сканы</b>\n\nСвежих сканов пока нет. После завершения они будут храниться здесь 24 часа, затем уйдут в Архив."
        )
    else:
        text = (
            "<b>📊 Мои сканы</b>\n\nТекущие и свежие запуски за последние <b>24 часа</b>. Старые автоматически уходят в Архив; данные не удаляются."
        )
    await _edit_or_answer(
        callback.message,
        text,
        reply_markup=my_scans_keyboard(scans, archive_count),
    )


@dp.callback_query(F.data == "archive_my_scans")
async def archive_my_scans(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    moved = await archive_active_finished_scans(callback.from_user.id)
    scans = await get_user_scans(callback.from_user.id, 10)
    archive_count = await get_archive_count(callback.from_user.id)
    await callback.answer(f"В архив перемещено: {moved}")
    await callback.message.edit_text(
        "<b>📊 Мои сканы</b>\n\n"
        f"📦 Перемещено в архив: <b>{moved}</b>.\n"
        "Активный/ожидающий парсинг остаётся здесь. Данные сканов не удаляются.",
        parse_mode=ParseMode.HTML,
        reply_markup=my_scans_keyboard(scans, archive_count),
    )


@dp.callback_query(F.data.startswith("scan_archive:"))
async def scan_archive(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    try:
        page = max(0, int(callback.data.split(":", 1)[1]))
    except Exception:
        page = 0
    scans, total = await get_user_archive(callback.from_user.id, page)
    if total and not scans and page > 0:
        page = max(0, (total - 1) // SCAN_ARCHIVE_PAGE_SIZE)
        scans, total = await get_user_archive(callback.from_user.id, page)
    await callback.answer()
    text = (
        "<b>📦 Архив сканов</b>\n\n"
        f"Всего: <b>{total}</b>. Здесь хранятся сканы старше 24 часов и те, "
        "которые ты убрал вручную.\n\n"
        "Архив <b>не удаляет данные</b>: история просмотров и аналитика «Популярное сейчас» сохраняются."
    )
    await callback.message.edit_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=scan_archive_keyboard(scans, page, total),
    )


@dp.callback_query(F.data == "archive_noop")
async def archive_noop(callback: CallbackQuery) -> None:
    await callback.answer()


async def render_scan_detail(scan: UserScan) -> str:
    """Compact product-style scan card. Show diagnostics only when actionable."""
    pairs = await get_scan_rows(scan.id)
    scan_settings = await get_settings(scan.user_id)
    allowed_rows = apply_listing_settings(
        [listing for listing, _ in pairs], scan_settings, exact_date_scan=True, apply_output_mode=True
    )
    allowed_ids = {row.external_id for row in allowed_rows}
    pairs = [pair for pair in pairs if pair[0].external_id in allowed_ids]
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

    history_rounds = await get_scan_history_rounds(scan.id, limit=50)
    observation_statuses = await get_scan_observation_statuses(scan.id)
    status_icons = {"done": "✅", "pending": "⏳", "running": "🔄", "missed": "▫️", "error": "⚠️"}
    observation_line = " · ".join(
        f"{hours}ч{status_icons.get(observation_statuses.get(hours, 'pending'), '⏳')}"
        for hours in OBSERVATION_HOURS
    )

    quality_value = int(getattr(scan, "quality_score", 0) or 0)
    status_label = {
        "done": "✅ Завершён",
        "partial": "⚠️ Частичный результат",
        "running": "🔄 Выполняется",
        "queued": "⏳ Ожидает",
        "cancelling": "⏹ Останавливается",
        "cancelled": "⏹ Остановлен",
        "failed": "❌ Ошибка",
    }.get(scan.status, scan.status)
    depth = scan.page_limit if scan.page_limit in PAGE_LIMIT_CHOICES else 50

    lines = [
        f"<b>📊 {html.escape(scan.title)}</b>",
        status_label,
        "",
        f"📅 <b>{_date_label(scan.target_date)}</b> · 📄 <b>{depth} стр.</b>",
        f"📦 Объявлений: <b>{len(rows)}</b> · 👁 С просмотрами: <b>{viewed}</b>",
    ]
    if scan.total_categories > 1:
        lines.append(f"🗂 Категории: <b>{scan.completed_categories}/{scan.total_categories}</b>")

    if growers > 0:
        lines.append(f"🚀 Набирают просмотры: <b>{growers}</b> · всего <b>+{total_growth}</b>")
    elif len(history_rounds) < 2:
        lines.append("🚀 Рост появится после первого контрольного замера")
    if disappeared > 0:
        lines.append(f"▫️ Исчезли: <b>{disappeared}</b>")

    lines += [
        "",
        f"🔔 Автозамеры: <b>{observation_line}</b>",
    ]
    if scan.last_view_refresh_at:
        lines.append(f"🕒 Обновлено: <b>{_moscow_text(scan.last_view_refresh_at)} МСК</b>")

    if scan.status == "partial":
        incomplete_keys = [
            key for key in (getattr(scan, "incomplete_category_keys", "") or "").split(",") if key in CATEGORIES
        ]
        if incomplete_keys:
            names = ", ".join(CATEGORIES[key].name for key in incomplete_keys[:5])
            lines += ["", f"⚠️ Допроверка: <b>{len(incomplete_keys)}</b> · {html.escape(names)}"]
        else:
            lines += ["", "⚠️ Часть скана требует допроверки."]
    elif quality_value and quality_value < 90 and getattr(scan, "quality_note", ""):
        lines += ["", f"⚠️ Проверка качества: {html.escape(scan.quality_note)}"]

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
            text, parse_mode=ParseMode.HTML, reply_markup=scan_detail_keyboard(scan.id, archived=scan.archived_at is not None, recheck=bool(getattr(scan, "incomplete_category_keys", ""))), disable_web_page_preview=True
        )
    else:
        await callback.message.answer(
            text, parse_mode=ParseMode.HTML, reply_markup=scan_detail_keyboard(scan.id, archived=scan.archived_at is not None, recheck=bool(getattr(scan, "incomplete_category_keys", ""))), disable_web_page_preview=True
        )


@dp.callback_query(F.data.startswith("scanproducts:"))
async def scan_products(callback: CallbackQuery) -> None:
    """Legacy callback from old messages. The model section was removed in v3.1.6."""
    try:
        scan_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Скан не найден", show_alert=True); return
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    await callback.answer("Раздел «Модели» убран — открыл карточку скана")
    await callback.message.answer(
        await render_scan_detail(scan),
        parse_mode=ParseMode.HTML,
        reply_markup=scan_detail_keyboard(scan_id, archived=scan.archived_at is not None, recheck=bool(getattr(scan, "incomplete_category_keys", ""))),
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data.startswith("scantop:"))
async def scan_top(callback: CallbackQuery) -> None:
    scan_id = int(callback.data.split(":", 1)[1])
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return
    pairs = await get_scan_rows(scan_id)
    scan_settings = await get_settings(callback.from_user.id)
    allowed_rows = apply_listing_settings(
        [p[0] for p in pairs], scan_settings, exact_date_scan=True, apply_output_mode=True
    )
    allowed_ids = {row.external_id for row in allowed_rows}
    pairs = [p for p in pairs if p[0].external_id in allowed_ids and p[0].view_count is not None]
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
    await callback.message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=scan_detail_keyboard(scan_id, archived=scan.archived_at is not None, recheck=bool(getattr(scan, "incomplete_category_keys", ""))))


@dp.callback_query(F.data.startswith("scangrowth:"))
async def scan_growth(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    try:
        scan_id = int(parts[1])
        period_hours = int(parts[2]) if len(parts) > 2 else 3
    except Exception:
        await callback.answer("Скан не найден", show_alert=True); return
    if period_hours not in OBSERVATION_HOURS:
        period_hours = 3
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True); return

    growth, rounds = await get_scan_growth_rows(scan_id, period_hours)
    scan_settings = await get_settings(callback.from_user.id)
    allowed_growth_rows = apply_listing_settings(
        [item.listing for item in growth], scan_settings, exact_date_scan=True, apply_output_mode=True
    )
    allowed_growth_ids = {row.external_id for row in allowed_growth_rows}
    growth = [item for item in growth if item.listing.external_id in allowed_growth_ids]
    await callback.answer()
    period_label = f"{period_hours} ч"
    if not growth:
        text = (
            f"🚀 <b>TOP роста за {period_label}</b>\n\n"
            "Контрольный замер для этого периода ещё не готов или прироста пока нет. "
            "Бот автоматически делает замеры через 3 / 6 / 12 часов после первого скана."
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
            lines.append(
                f"<b>{i}. {html.escape(row.title[:60])}</b>\n"
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
    ws.merge_cells("A1:L1")
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append([
        "#", "Товар", "Категория", "Цена €",
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
            idx, row.title, row.category, row.price_eur,
            item.base_views, item.current_views, item.delta, round(item.per_hour, 2),
            round(item.elapsed_hours, 2), row.posted_date_msk or row.posted_text or "",
            row.external_id, row.url,
        ])
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:L{max(2, ws.max_row)}"
    widths = {
        "A": 6, "B": 44, "C": 26, "D": 11, "E": 16, "F": 17,
        "G": 12, "H": 16, "I": 19, "J": 16, "K": 16, "L": 52,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    for row_cells in ws.iter_rows(min_row=3):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    growth_fill = PatternFill("solid", fgColor="E2F0D9")
    for cell in ws["G"][2:]:
        cell.fill = growth_fill
        cell.font = Font(bold=True)
    for cell in ws["L"][2:]:
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
    scan_settings = await get_settings(scan.user_id)
    allowed_rows = apply_listing_settings(
        [item.listing for item in growth], scan_settings, exact_date_scan=True, apply_output_mode=True
    )
    allowed_ids = {row.external_id for row in allowed_rows}
    growth = [item for item in growth if item.listing.external_id in allowed_ids]
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


async def send_category_growth_xlsx(
    message: Message, user_id: int, category_key: str, period_hours: int
) -> None:
    scans = await get_user_category_scans(user_id, category_key)
    growth, scan_count, _ = await get_category_growth_rows(user_id, category_key, period_hours)
    scan_settings = await get_settings(user_id)
    allowed_rows = apply_listing_settings(
        [item.listing for item in growth], scan_settings, exact_date_scan=True, apply_output_mode=True
    )
    allowed_ids = {row.external_id for row in allowed_rows}
    growth = [item for item in growth if item.listing.external_id in allowed_ids]
    if not scans or not growth:
        await message.answer(
            f"📊 TOP-{GROWTH_TOP_LIMIT} за {period_hours}ч пока нельзя сформировать: "
            "контрольные замеры ещё не готовы или прироста нет."
        )
        return
    representative = scans[0]
    path = build_growth_top_xlsx(
        representative, period_hours, growth, category_key=category_key
    )
    try:
        cat = CATEGORIES.get(category_key)
        suffix = f" · {cat.name}" if cat else ""
        await message.answer_document(
            FSInputFile(path),
            caption=(
                f"📊 TOP-{min(GROWTH_TOP_LIMIT, len(growth))} роста за {period_hours}ч{suffix} "
                "· последний успешный скан"
            ),
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
        representative_scan_id, category_key, period_hours = int(parts[1]), parts[2], int(parts[3])
    except Exception:
        await callback.answer("Некорректный запрос", show_alert=True); return
    if period_hours not in OBSERVATION_HOURS or category_key not in CATEGORIES:
        await callback.answer("Некорректный запрос", show_alert=True); return
    scans = await get_user_category_scans(callback.from_user.id, category_key)
    if not scans:
        await callback.answer("Сканы категории не найдены", show_alert=True); return
    await callback.answer("Формирую TOP-50 последнего скана")
    await send_category_growth_xlsx(
        callback.message, callback.from_user.id, category_key, period_hours
    )


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
    await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=scan_detail_keyboard(scan_id, archived=scan.archived_at is not None, recheck=bool(getattr(scan, "incomplete_category_keys", ""))))


async def _manual_view_refresh_job(
    bot: Bot, user_id: int, scan_id: int, progress_message: Message | None = None
) -> None:
    try:
        scan = await get_user_scan(user_id, scan_id)
        if scan is None:
            return
        pairs = await get_scan_rows(scan_id)
        rows = [row for row, _ in pairs]
        if not rows:
            if progress_message is not None:
                await progress_message.edit_text("ℹ️ В этом скане пока нет объявлений для обновления.")
            else:
                await bot.send_message(user_id, "ℹ️ В этом скане пока нет объявлений для обновления.")
            return

        title = html.escape(scan.title)
        measurement_started = datetime.utcnow() - timedelta(seconds=VIEW_MEASUREMENT_REUSE_SECONDS)

        # A manual control measurement is fresh: the normal 5/30-minute view cache
        # cannot substitute it. Only values fetched in the last few seconds may be
        # shared with another simultaneous user's measurement.
        async with background_view_refresh_lock:
            requested, updated, failed = await refresh_view_counts(
                rows, None, force=False, max_age_seconds=VIEW_MEASUREMENT_REUSE_SECONDS,
                traffic_priority="background",
                progress_message=progress_message,
                progress_title=f"👁 Повторный замер · {scan.title}",
            )
            recorded = await update_scan_view_refresh(
                scan_id, fresh_after=measurement_started
            )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть динамику", callback_data=f"scangrowth:{scan_id}:3"),
             InlineKeyboardButton(text="🔥 Топ", callback_data=f"scantop:{scan_id}")],
            [InlineKeyboardButton(text="📊 Открыть этот скан", callback_data=f"scan:{scan_id}")],
        ])

        if recorded <= 0:
            text = (
                f"⚠️ <b>Замер не выполнен</b>\n\n"
                f"Скан: <b>{title}</b>\n"
                f"📦 Объявлений: <b>{len(rows)}</b>\n"
                f"👁 Свежих значений получить не удалось.\n\n"
                "Новая точка наблюдения не создана. Остальными разделами бота можно пользоваться как обычно."
            )
            if progress_message is not None:
                await progress_message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            else:
                await bot.send_message(user_id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return

        reused = max(0, recorded - updated)
        grew_count, max_delta, total_delta = await get_latest_manual_growth_summary(scan_id)
        text = (
            f"✅ <b>Просмотры обновлены</b>\n\n"
            f"Скан: <b>{title}</b>\n"
            f"📦 Объявлений: <b>{len(rows)}</b>\n"
            f"👁 Свежих значений: <b>{recorded}</b>\n"
            f"⚡ Получено direct-запросами: <b>{updated}</b>\n"
            f"♻️ Переиспользовано одновременно свежих: <b>{reused}</b>\n"
            f"▫️ Без данных: <b>{max(0, len(rows) - recorded)}</b>\n\n"
            f"🚀 Выросли с прошлого замера: <b>{grew_count}</b>\n"
            f"🔥 Максимальный прирост: <b>+{max_delta}</b>\n"
            f"📈 Суммарный прирост: <b>+{total_delta}</b>\n"
            f"🕐 Замер: <b>{_moscow_text(datetime.utcnow())} МСК</b>\n\n"
            "TOP и динамика уже пересчитаны по этой реальной точке наблюдения."
        )
        if progress_message is not None:
            await progress_message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            await bot.send_message(user_id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        log.exception("Manual background view refresh failed scan=%s", scan_id)
        try:
            text = (
                "⚠️ Не удалось полностью обновить просмотры. Новая точка наблюдения не будет считаться готовой; "
                "остальные разделы бота продолжают работать."
            )
            if progress_message is not None:
                await progress_message.edit_text(text)
            else:
                await bot.send_message(user_id, text)
        except Exception:
            pass
    finally:
        async with manual_view_tasks_guard:
            current = manual_view_tasks.get(scan_id)
            if current is asyncio.current_task() or (current is not None and current.done()):
                manual_view_tasks.pop(scan_id, None)


@dp.callback_query(F.data.startswith("scanviews:"))
async def scan_refresh_views(callback: CallbackQuery, bot: Bot) -> None:
    scan_id = int(callback.data.split(":", 1)[1])
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True)
        return
    pairs = await get_scan_rows(scan_id)
    if not pairs:
        await callback.answer("В этом скане пока нет объявлений", show_alert=True)
        return

    async with manual_view_tasks_guard:
        existing = manual_view_tasks.get(scan_id)
        if existing is not None and not existing.done():
            await callback.answer("👁 Просмотры этого скана уже обновляются в фоне")
            return

        # This is a separate message, so navigating menus cannot overwrite it.
        progress_message = await callback.message.answer(
            f"<b>👁 Повторный замер · {html.escape(scan.title)}</b>\n\n"
            f"{_progress_bar(0)} <b>0%</b>\n"
            f"📦 Проверено: <b>0/{len(pairs)}</b>\n\n"
            "Можно сразу переходить в другие разделы — замер работает в фоне.",
            parse_mode=ParseMode.HTML,
        )
        task = asyncio.create_task(
            _manual_view_refresh_job(bot, callback.from_user.id, scan_id, progress_message),
            name=f"manual-view-refresh-{scan_id}",
        )
        manual_view_tasks[scan_id] = task

    # Handler returns immediately; the progress message is edited at most every ~1.5 s.
    await callback.answer("👁 Замер запущен в фоне")


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
    if len(keys) > MAX_SELECTED_CATEGORIES:
        await callback.answer(
            f"Этот старый скан содержит {len(keys)} категорий. Сейчас лимит — {MAX_SELECTED_CATEGORIES}; выбери нужные категории для нового запуска.",
            show_alert=True,
        )
        return
    repeat_depth = scan.page_limit if scan.page_limit in PAGE_LIMIT_CHOICES else 50
    await callback.answer("Повторяю скан")
    await enqueue_user_scan(callback.message, callback.from_user.id, keys, repeat_depth, scan.target_date or _moscow_today_iso())


@dp.callback_query(F.data.startswith("scanrecheck:"))
async def scan_recheck_partial(callback: CallbackQuery) -> None:
    """Re-run only categories that were not fully verified in a partial scan."""
    try:
        scan_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Скан не найден", show_alert=True)
        return
    scan = await get_user_scan(callback.from_user.id, scan_id)
    if scan is None:
        await callback.answer("Скан не найден", show_alert=True)
        return

    keys = [
        key for key in (getattr(scan, "incomplete_category_keys", "") or "").split(",")
        if key in CATEGORIES
    ]
    keys = list(dict.fromkeys(keys))
    if not keys:
        await callback.answer("У этого скана нет категорий, требующих допроверки", show_alert=True)
        return
    if len(keys) > MAX_SELECTED_CATEGORIES:
        keys = keys[:MAX_SELECTED_CATEGORIES]

    async with job_guard:
        existing = active_jobs.get(callback.from_user.id)
        if existing and existing.state in {"queued", "running"} and not existing.cancel_requested:
            await callback.answer("У тебя уже идёт парсинг", show_alert=True)
            return
        if len(queued_job_ids) >= MAX_QUEUE_SIZE:
            await callback.answer("Сервис сейчас сильно загружен. Попробуй чуть позже.", show_alert=True)
            return

    depth = scan.page_limit if scan.page_limit in PAGE_LIMIT_CHOICES else 50
    target_date = scan.target_date or _moscow_today_iso()
    # Do not immediately reuse the partial 5-minute category result cache.
    for key in keys:
        category_result_cache.pop(_progress_key(key, target_date, depth), None)

    await callback.answer(f"Допроверяю категорий: {len(keys)}")
    await enqueue_user_scan(callback.message, callback.from_user.id, keys, depth, target_date)


@dp.callback_query(F.data == "groups")
async def groups(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id): await callback.answer("Нет доступа", show_alert=True); return
    selected = await get_selected(callback.from_user.id)
    await callback.answer()
    await _edit_or_answer(
        callback.message,
        f"<b>🗂 Категории</b>\n\nВыбери до <b>{MAX_SELECTED_CATEGORIES}</b> категорий на один скан.",
        reply_markup=groups_keyboard(selected),
    )


@dp.callback_query(F.data.startswith("grp:"))
async def open_group(callback: CallbackQuery) -> None:
    group_key = callback.data.split(":", 1)[1]
    if group_key not in GROUPS: await callback.answer("Раздел не найден", show_alert=True); return
    selected = await get_selected(callback.from_user.id)
    group = GROUPS[group_key]
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{group.icon} {html.escape(group.name)}</b>\n\n"
        f"Отметь нужные подкатегории. Максимум за один запуск: <b>{MAX_SELECTED_CATEGORIES}</b>.",
        reply_markup=category_keyboard(group_key, selected),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("cat:"))
async def toggle_cat(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    if key not in CATEGORIES: await callback.answer("Категория не найдена", show_alert=True); return
    selected, limit_reached = await toggle_category(callback.from_user.id, key)
    if limit_reached:
        await callback.answer(
            f"Можно выбрать максимум {MAX_SELECTED_CATEGORIES} категорий за один запуск. Сними одну галочку и выбери другую.",
            show_alert=True,
        )
    else:
        await callback.answer(f"Выбрано: {len(selected)}/{MAX_SELECTED_CATEGORIES}")
    await callback.message.edit_reply_markup(reply_markup=category_keyboard(CATEGORIES[key].group, selected))


@dp.callback_query(F.data.startswith("grpall:"))
async def toggle_all_children(callback: CallbackQuery) -> None:
    group_key = callback.data.split(":", 1)[1]
    if group_key not in GROUPS: return
    selected, limit_reached = await toggle_group_children(callback.from_user.id, group_key)
    if limit_reached:
        await callback.answer(
            f"Выбраны свободные места до лимита {MAX_SELECTED_CATEGORIES}. Для одного скана больше нельзя.",
            show_alert=True,
        )
    else:
        await callback.answer(f"Выбрано: {len(selected)}/{MAX_SELECTED_CATEGORIES}")
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
        counter = f"{len(cats)}/{MAX_SELECTED_CATEGORIES}"
        lines = [f"<b>Выбрано категорий: {counter}</b>", ""]
        if len(cats) > MAX_SELECTED_CATEGORIES:
            lines += [f"⚠️ Для нового запуска оставь максимум {MAX_SELECTED_CATEGORIES} категорий.", ""]
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


async def request_user_scan_stop(user_id: int, job_id: str | None = None) -> tuple[ScanJob | None, str]:
    """Set and persist the hard-stop signal used by both the button and /stop."""
    scan_id: int | None = None
    async with job_guard:
        job = active_jobs.get(user_id)
        if job is None or (job_id is not None and job.job_id != job_id):
            return None, "missing"
        if job.state not in {"queued", "running"}:
            return None, "finished"
        previous = job.state
        job.cancel_requested = True
        job.stop_event.set()
        scan_id = job.scan_id
        if job.job_id in queued_job_ids:
            queued_job_ids.remove(job.job_id)

    # Persist intent immediately. If Railway restarts before finish_job(), startup
    # recovery sees 'cancelling' and finalizes it as cancelled instead of resuming.
    if scan_id is not None:
        async with SessionLocal() as session:
            scan = await session.get(UserScan, int(scan_id))
            if scan is not None and scan.status in {"queued", "running"}:
                scan.status = "cancelling"
                scan.last_error = "Остановка запрошена пользователем"
                await session.commit()
    return job, previous


@dp.message(Command("stop"))
async def stop_scan_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    job, previous_state = await request_user_scan_stop(message.from_user.id)
    if job is None:
        await message.answer(
            "✅ Сейчас активного парсинга нет.",
            reply_markup=main_keyboard(len(await get_selected(message.from_user.id))),
        )
        return
    if previous_state == "queued":
        await message.answer(
            "⏹ <b>Парсинг остановлен.</b> Задание снято до начала сетевого сканирования.",
            parse_mode=ParseMode.HTML,
            reply_markup=stopped_job_keyboard(),
        )
    else:
        await message.answer(
            "⏹ <b>Останавливаю парсер прямо сейчас…</b>\n\n"
            "Новые страницы и объявления больше не будут запускаться. Можно сразу выбрать другую категорию.",
            parse_mode=ParseMode.HTML,
            reply_markup=stopped_job_keyboard(),
        )


@dp.callback_query(F.data.startswith("cancel_scan:"))
async def cancel_scan(callback: CallbackQuery) -> None:
    job_id = callback.data.split(":", 1)[1]
    job, previous_state = await request_user_scan_stop(callback.from_user.id, job_id)
    if job is None:
        await callback.answer("Активная задача уже не найдена", show_alert=True)
        return

    if previous_state == "queued":
        await callback.answer("Парсинг остановлен")
        await callback.message.edit_text(
            "⏹ <b>Парсинг остановлен</b>\n\nЗадание снято до начала сканирования.",
            parse_mode=ParseMode.HTML,
            reply_markup=stopped_job_keyboard(),
        )
    else:
        await callback.answer("Останавливаю парсер")
        await callback.message.edit_text(
            "⏹ <b>Останавливаю парсер прямо сейчас…</b>\n\n"
            "Текущий сетевой скан отменяется. Можно сразу выбрать другую категорию.",
            parse_mode=ParseMode.HTML,
            reply_markup=stopped_job_keyboard(),
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
    if len(selected_cats) > MAX_SELECTED_CATEGORIES:
        await callback.answer(
            f"Сейчас выбрано {len(selected_cats)} категорий. В новой версии максимум {MAX_SELECTED_CATEGORIES} за один запуск — убери лишние или очисти выбор.",
            show_alert=True,
        )
        return

    async with job_guard:
        existing = active_jobs.get(callback.from_user.id)
        if existing and existing.state in {"queued", "running"} and not existing.cancel_requested:
            await callback.answer("У тебя уже идёт парсинг", show_alert=True)
            return

    await state.set_state(ScanInput.target_date)
    await callback.answer()
    await callback.message.answer(
        "<b>▶️ Новый скан</b>\n\n"
        "<b>1/2 · Дата объявлений</b>\n"
        "Выбери день. Время считаем по Москве.",
        parse_mode=ParseMode.HTML,
        reply_markup=scan_date_keyboard(),
    )


async def _show_scan_depth_choice(message: Message, state: FSMContext, user_id: int, target_date: str) -> None:
    await state.update_data(target_date=target_date)
    selected = await get_selected(user_id)
    selected_cats = [CATEGORIES[k] for k in CATEGORIES if k in selected]
    if not selected_cats:
        await state.clear()
        await message.answer("Сначала выбери хотя бы одну категорию.")
        return
    if len(selected_cats) > MAX_SELECTED_CATEGORIES:
        await state.clear()
        await message.answer(
            f"⚠️ Сейчас выбрано <b>{len(selected_cats)}</b> категорий, а максимум для одного запуска — "
            f"<b>{MAX_SELECTED_CATEGORIES}</b>. Убери лишние категории и запусти снова.",
            parse_mode=ParseMode.HTML,
            reply_markup=groups_keyboard(selected),
        )
        return

    scan_settings = await get_settings(user_id)
    include = html.escape(scan_settings.include_words) if scan_settings.include_words else ""
    exclude = html.escape(scan_settings.exclude_words) if scan_settings.exclude_words else ""
    extra_lines = []
    if include:
        extra_lines.append(f"🔎 Ключевые: <b>{include}</b>")
    if exclude:
        extra_lines.append(f"🚫 Исключения: <b>{exclude}</b>")
    extras = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
    await message.answer(
        "<b>▶️ Новый скан</b>\n\n"
        "<b>2/2 · Проверка запуска</b>\n"
        f"📅 Дата: <b>{_date_label(target_date)}</b>\n"
        f"🗂 Категорий: <b>{len(selected_cats)}/{MAX_SELECTED_CATEGORIES}</b>\n"
        f"Режим: <b>{MODE_LABELS.get(scan_settings.output_mode, scan_settings.output_mode)}</b>\n"
        f"💶 Цена: <b>{PRICE_LABELS.get(scan_settings.price_filter, scan_settings.price_filter)}</b> · "
        f"👁 <b>{min_views_label(getattr(scan_settings, 'min_views', 0))}</b>\n"
        f"🧠 Дубли: <b>{'Вкл' if scan_settings.smart_dedupe else 'Выкл'}</b> · "
        f"🧹 Шум: <b>{'Вкл' if scan_settings.clean_noise else 'Выкл'}</b>"
        f"{extras}\n\n"
        "Выбери глубину сканирования.",
        parse_mode=ParseMode.HTML,
        reply_markup=page_limit_keyboard(),
    )


@dp.callback_query(F.data.startswith("scan_date:"))
async def choose_scan_date(callback: CallbackQuery, state: FSMContext) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    choice = callback.data.split(":", 1)[1]
    today = datetime.now(MOSCOW).date()
    if choice == "today":
        target_date = today.isoformat()
    elif choice == "yesterday":
        target_date = (today - timedelta(days=1)).isoformat()
    elif choice == "custom":
        await state.set_state(ScanInput.target_date)
        await callback.answer()
        await callback.message.answer(
            "<b>🗓 Своя дата</b>\n\n"
            "Отправь <code>10.08.2026</code>, <code>10.08</code> или просто <code>10</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    else:
        await callback.answer("Неизвестная дата", show_alert=True)
        return

    await callback.answer()
    await _show_scan_depth_choice(callback.message, state, callback.from_user.id, target_date)


@dp.message(ScanInput.target_date)
async def receive_scan_date(message: Message, state: FSMContext) -> None:
    if not allowed(message.from_user.id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    target_date = _parse_scan_date_input(message.text)
    if target_date is None:
        await message.answer(
            "⚠️ Не понял дату. Отправь, например, <code>12</code>, <code>10.08</code> или <code>10.08.2026</code>. "
            "Будущую дату выбрать нельзя.",
            parse_mode=ParseMode.HTML,
        )
        return
    await _show_scan_depth_choice(message, state, message.from_user.id, target_date)


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
    if len(selected_cats) > MAX_SELECTED_CATEGORIES:
        await callback.answer(
            f"Сейчас выбрано {len(selected_cats)} категорий. В новой версии максимум {MAX_SELECTED_CATEGORIES} за один запуск — убери лишние или очисти выбор.",
            show_alert=True,
        )
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
    await callback.answer("Скан запущен")
    await enqueue_user_scan(
        callback.message, callback.from_user.id, [cat.key for cat in selected_cats], page_limit, target_date
    )


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    await init_db()
    await initialize_commerce()
    backfilled = await backfill_product_identities()
    if backfilled:
        log.info("v3.0 product identity backfill: %s listings", backfilled)
    obsolete_observations = await cleanup_obsolete_observation_plans()
    recovered_observations = await recover_running_observations()
    planned = await backfill_recent_observation_plans()
    archived = await archive_expired_scans()
    if recovered_observations or planned or obsolete_observations:
        log.info(
            "v3.3.0 observations: removed_old=%s recovered=%s recent_scans_planned=%s",
            obsolete_observations, recovered_observations, planned,
        )
    if archived:
        log.info("v3.3.0 initial scan archive: %s moved", archived)

    bot = Bot(BOT_TOKEN)
    try:
        await setup_bot_commands(bot)
    except Exception:
        # A Telegram menu configuration error must never keep the parser offline.
        log.exception("Could not configure Telegram command menu")

    recovered_scans = await recover_interrupted_user_scans(bot)
    if recovered_scans:
        log.warning("v3.3.0 recovered %s unfinished user scan(s) after restart", recovered_scans)

    me = await bot.get_me()
    traffic = await TRAFFIC.snapshot()
    log.info(
        "Starting @%s | workers=%s cache_ttl=%ss | traffic scan=%s view=%s browser=%s global=%s",
        me.username, MAX_CONCURRENT_JOBS, CATEGORY_CACHE_TTL_SECONDS,
        traffic.scan_limit, traffic.view_limit, traffic.browser_limit, traffic.global_limit,
    )
    log.info("Database backend: %s", DATABASE_BACKEND)

    worker_tasks = [
        asyncio.create_task(scan_worker(bot, i), name=f"scan-worker-{i}")
        for i in range(1, MAX_CONCURRENT_JOBS + 1)
    ]
    ticker_task = asyncio.create_task(progress_ticker(bot), name="user-progress-ticker")
    payment_task = asyncio.create_task(payment_scheduler(bot), name="payment-scheduler")
    subscription_task = asyncio.create_task(
        subscription_lifecycle_scheduler(bot), name="subscription-lifecycle-scheduler"
    )
    archive_task = asyncio.create_task(scan_archive_scheduler(), name="scan-archive-scheduler")
    observation_tasks = [
        asyncio.create_task(observation_scheduler(bot, i), name=f"view-observation-worker-{i}")
        for i in range(1, OBSERVATION_CONCURRENCY + 1)
    ]
    try:
        await dp.start_polling(bot)
    finally:
        ticker_task.cancel()
        payment_task.cancel()
        subscription_task.cancel()
        archive_task.cancel()
        for task in observation_tasks:
            task.cancel()
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(
            ticker_task, payment_task, subscription_task, archive_task,
            *observation_tasks, *worker_tasks, return_exceptions=True,
        )
        async with category_inflight_guard:
            inflight = list(category_inflight.values())
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
