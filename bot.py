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
from models import CategoryScanState, Listing, ParserRun, PriceHistory, SelectedCategory, UserSettings
from parser import (
    MAX_PAGES_PER_CATEGORY,
    PAGE_DELAY_SECONDS,
    STOP_AFTER_EMPTY_TODAY_PAGES,
    KleinanzeigenParser,
    ParsedListing,
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


class SettingsInput(StatesGroup):
    include_words = State()
    exclude_words = State()


def allowed(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def main_keyboard(selected_count: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать парсинг", callback_data="start_scan")],
        [InlineKeyboardButton(text=f"🗂 Категории ({selected_count})", callback_data="groups")],
        [InlineKeyboardButton(text="⚙️ Настройки парсинга", callback_data="settings")],
        [InlineKeyboardButton(text="📦 Получить результат", callback_data="export_smart")],
        [InlineKeyboardButton(text="📥 Очередь", callback_data="queue_status"),
         InlineKeyboardButton(text="📊 База", callback_data="stats")],
        [InlineKeyboardButton(text="📋 Что выбрано", callback_data="selected")],
    ])


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
        writer.writerow(["Категория", "Название", "Цена", "Цена, €", "Дата публикации", "Ссылка"])
        for row in rows:
            writer.writerow([
                row.category, row.title, _price_display(row.price_text, row.price_eur),
                row.price_eur if row.price_eur is not None else "",
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
            "Категория", "Название", "Цена", "Цена, €", "Медиана группы, €",
            "Ниже медианы, %", "Образцов", "Точность группы, %", "Дата", "Ссылка",
        ])
        for row in rows:
            writer.writerow([
                row.category, row.title, row.price_text or f"{row.price_eur} €", row.price_eur,
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


async def send_smart_export(message: Message, user_id: int, selected_count: int) -> int:
    s = await get_settings(user_id)
    mode = s.output_mode
    all_rows = await today_rows()
    selected_keys = await get_selected(user_id)
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
        f"Ключевые слова: <b>{include}</b>\n"
        f"Исключить: <b>{exclude}</b>\n\n"
        "<i>v2.6 сохраняет Smart Analytics/Fast Incremental и добавляет общую очередь, кэш категорий и совместные сканы. "
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
    async with job_guard:
        running_jobs_count = sum(1 for j in active_jobs.values() if j.state == "running")
        queued_jobs_count = sum(1 for j in active_jobs.values() if j.state == "queued" and not j.cancel_requested)
        inflight_categories_count = len(category_inflight)
    return (
        f"<b>📊 База и парсинг</b>\n\n"
        f"Сегодня собрано: <b>{today}</b>\n"
        f"С ценой: <b>{priced}</b> ({coverage}%)\n"
        f"Всего сохранено: <b>{total}</b>\n"
        f"Записей истории цен: <b>{drops}</b>\n\n"
        f"<b>⚡ v2.6 сегодня</b>\n"
        f"Запусков категорий: <b>{runs}</b>\n"
        f"Быстрых запусков: <b>{fast_runs}</b>\n"
        f"Категорий готовы к fast-mode: <b>{fast_ready}</b>\n"
        f"Пройдено страниц: <b>{pages}</b>\n"
        f"Найдено новых за запуски: <b>{scan_new}</b>\n\n"
        f"<b>📥 Multi-User Core</b>\n"
        f"Воркеров: <b>{MAX_CONCURRENT_JOBS}</b>\n"
        f"В работе: <b>{running_jobs_count}</b> · В очереди: <b>{queued_jobs_count}</b>\n"
        f"Категорий сейчас сканируется: <b>{inflight_categories_count}</b>\n"
        f"TTL кэша: <b>{CATEGORY_CACHE_TTL_SECONDS} сек.</b>\n\n"
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
            state.day_seed_complete = seed_complete
            if seed_complete:
                state.day_full_pages = max(state.day_full_pages or 0, pages_scanned)
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


async def scan_one_category(parser: KleinanzeigenParser, cat, user_id: int) -> ScanResult:
    """Scan one category.

    v2.5 uses two modes:
    - full: once per Berlin day/category, walk the current-day feed until Heute ends;
    - fast: later scans start at page 1 and stop after reaching the previous head
      checkpoint plus a safety overlap, or after several highly-known pages.

    The fast mode never skips the first pages: this keeps it correct when many new
    listings arrived since the previous run. It only avoids re-reading the old tail.
    """
    state = await get_category_scan_state(cat.key)
    day_key = berlin_date_key()
    can_fast = bool(state and state.scan_date == day_key and state.day_seed_complete)
    mode = "fast" if can_fast else "full"
    previous_heads = {
        x for x in ((state.head_ids if state else "") or "").split(",") if x
    }
    baseline_pages = (state.day_full_pages or 0) if state and state.scan_date == day_key else 0

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
        for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
            items = await parser.parse_category_page(page_url(cat.url, page))
            pages_scanned = page
            if not items:
                reason = "конец выдачи"
                break

            today_items = [item for item in items if is_today_text(item.posted_text)]
            today_seen += len(today_items)

            if page == 1 and today_items:
                first_page_head_ids = [item.external_id for item in today_items[:INCREMENTAL_HEAD_SIZE]]

            if today_items:
                empty_today_pages = 0
                page_ids = {item.external_id for item in today_items}

                async with db_write_lock:
                    new_items, known_count, enriched_count = await upsert_page_items(cat.key, cat.name, today_items)
                page_new = len(new_items)
                new_count += page_new
                known_total += known_count
                enriched_total += enriched_count

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
                    "prices_backfilled=%s checkpoint_page=%s known_pages=%s",
                    cat.name, mode, page, len(items), len(today_items), page_new, known_count,
                    known_ratio, enriched_count, checkpoint_seen_page, consecutive_known_pages,
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

            if PAGE_DELAY_SECONDS and page < MAX_PAGES_PER_CATEGORY:
                await asyncio.sleep(PAGE_DELAY_SECONDS)
        else:
            hit_limit = True
            reason = "аварийный лимит страниц"

        if not reason:
            reason = "завершено"

        # A full scan is considered seeded only if it reached a natural end, not
        # the emergency page cap. Fast mode keeps the previously completed seed.
        seed_complete = (mode == "full" and not hit_limit)
        saved = await save_category_scan_state(
            cat.key,
            mode=mode,
            pages_scanned=pages_scanned,
            new_count=new_count,
            today_seen=today_seen,
            reason=reason,
            head_ids=first_page_head_ids,
            seed_complete=seed_complete,
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
        [InlineKeyboardButton(text="❌ Отменить мой запуск", callback_data=f"cancel_scan:{job_id}")],
        [InlineKeyboardButton(text="📥 Состояние очереди", callback_data="queue_status")],
    ])


async def fresh_category_cache_age(category_key: str) -> int | None:
    """Return cache age in seconds when a category can be safely reused."""
    if CATEGORY_CACHE_TTL_SECONDS <= 0:
        return None
    state = await get_category_scan_state(category_key)
    if not state or not state.last_scan_at:
        return None
    if state.scan_date != berlin_date_key() or not state.day_seed_complete:
        return None
    age = max(0, int((datetime.utcnow() - state.last_scan_at).total_seconds()))
    if age <= CATEGORY_CACHE_TTL_SECONDS:
        return age
    return None


async def _scan_category_task(cat, user_id: int) -> ScanResult:
    parser = KleinanzeigenParser()
    try:
        return await scan_one_category(parser, cat, user_id)
    finally:
        await parser.close()


async def dispatch_category(cat, user_id: int) -> CategoryDispatchResult:
    """Use a fresh cache, join an in-flight scan, or start exactly one scan.

    This is the core v2.6 de-duplication layer: 15 users requesting Konsolen at
    the same moment still cause only one network scan of Konsolen.
    """
    cache_age = await fresh_category_cache_age(cat.key)
    if cache_age is not None:
        return CategoryDispatchResult(source="cache", cache_age_seconds=cache_age)

    async with category_inflight_guard:
        task = category_inflight.get(cat.key)
        if task is None:
            task = asyncio.create_task(_scan_category_task(cat, user_id), name=f"category-scan:{cat.key}")
            category_inflight[cat.key] = task
            source = "scan"
        else:
            source = "shared"

    try:
        result = await asyncio.shield(task)
        return CategoryDispatchResult(source=source, result=result)
    finally:
        if task.done():
            async with category_inflight_guard:
                if category_inflight.get(cat.key) is task:
                    category_inflight.pop(cat.key, None)


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
    today_count = len(await today_rows())
    if cancelled:
        title = "❌ <b>Парсинг отменён</b>"
    else:
        title = "✅ <b>Парсинг завершён</b>"
    text = (
        f"{title}\n\n"
        f"Категорий обработано: <b>{job.completed_categories}/{len(job.category_keys)}</b>\n"
        f"Новых объявлений обнаружено: <b>{job.total_new}</b>\n"
        f"Всего сегодня в общей базе: <b>{today_count}</b>\n\n"
        f"🌐 Реально просканировано категорий: <b>{job.scanned_categories}</b>\n"
        f"📄 Сетевых страниц: <b>{job.total_pages}</b>\n"
        f"⚡ Fast-mode: <b>{job.fast_categories}</b> · 📚 Full-mode: <b>{job.full_categories}</b>\n"
        f"🧠 Из свежего кэша: <b>{job.cache_hits}</b>\n"
        f"🤝 Подключено к уже идущему скану: <b>{job.shared_hits}</b>\n"
        f"⏩ Сэкономлено старых страниц: <b>{job.total_avoided}</b>\n\n"
        "Нажми <b>📦 Получить результат</b> — выборка сформируется по твоим настройкам."
    )
    job.state = "cancelled" if cancelled else "done"
    await edit_job_status(bot, job, text, force=True)
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
    job.worker_id = worker_id
    job.warnings = job.warnings or []
    await edit_job_status(
        bot,
        job,
        f"⚙️ <b>Парсинг запущен</b>\n\nВоркер: <b>#{worker_id}</b>\nКатегорий: <b>{len(job.category_keys)}</b>",
        force=True,
    )

    for idx, key in enumerate(job.category_keys, start=1):
        if job.cancel_requested:
            break
        cat = CATEGORIES.get(key)
        if cat is None:
            job.warnings.append(f"Неизвестная категория: {key}")
            continue
        job.current_category = cat.name
        try:
            dispatched = await dispatch_category(cat, job.user_id)
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
                    job.warnings.append(f"{cat.name}: достигнут аварийный лимит страниц")

            job.completed_categories += 1
            mode_line = ""
            if result is not None:
                mode_line = f"\nРежим категории: <b>{'⚡ быстрый' if result.mode == 'fast' else '📚 полный'}</b>"
            await edit_job_status(
                bot,
                job,
                "⏳ <b>Парсинг</b>\n\n"
                f"Категория: <b>{idx}/{len(job.category_keys)}</b>\n"
                f"Сейчас: <b>{html.escape(cat.name)}</b>\n"
                f"Источник: <b>{source_label}</b>{mode_line}\n\n"
                f"Новых обнаружено: <b>{job.total_new}</b>\n"
                f"Реальных сканов: <b>{job.scanned_categories}</b>\n"
                f"Из кэша: <b>{job.cache_hits}</b> · Общих: <b>{job.shared_hits}</b>\n"
                f"Сетевых страниц: <b>{job.total_pages}</b>",
            )
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




dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    selected = await get_selected(message.from_user.id)
    await message.answer(
        "<b>Kleinanzeigen Parser v2.6 · Multi-User Core</b>\n\n"
        "Запуски идут через общую очередь: свежие категории берутся из кэша, а одинаковые одновременные запросы объединяются в один скан.\n"
        "После сбора можешь менять режим выдачи без повторного парсинга.\n\n"
        "🆕 новые · 💎 уникальные · 🔥 частые · ⚡ быстро исчезающие · 💰 ниже рынка · 📉 снижение цены.",
        reply_markup=main_keyboard(len(selected)), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True); return
    selected = await get_selected(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text("<b>Kleinanzeigen Parser v2.6</b>\n\nВыбери действие:", reply_markup=main_keyboard(len(selected)), parse_mode=ParseMode.HTML)


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
        "<b>ℹ️ Критерии Smart Analytics v2.6</b>\n\n"
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
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    selected = await get_selected(callback.from_user.id)
    await callback.message.answer(
        await queue_status_text(callback.from_user.id),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(len(selected)),
    )


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
            "❌ <b>Задача отменена</b>\nОна не будет запускать новые категории.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await callback.answer("Отмена принята")
        await callback.message.edit_text(
            "⏳ <b>Отменяю запуск</b>\n\nТекущий общий скан категории, если он уже начался, не прерывается. "
            "После него твоя задача остановится.",
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
            await callback.answer("У тебя уже есть запуск в очереди или в работе", show_alert=True)
            return
        if len(queued_job_ids) >= MAX_QUEUE_SIZE:
            await callback.answer("Очередь временно заполнена. Попробуй чуть позже.", show_alert=True)
            return

    await callback.answer("Добавляю в очередь")
    status = await callback.message.answer(
        "⏳ <b>Добавляю задачу в очередь…</b>",
        parse_mode=ParseMode.HTML,
    )
    job = ScanJob(
        job_id=uuid.uuid4().hex[:12],
        user_id=callback.from_user.id,
        chat_id=callback.message.chat.id,
        status_message_id=status.message_id,
        category_keys=[cat.key for cat in selected_cats],
        created_at=datetime.utcnow(),
        warnings=[],
    )
    async with job_guard:
        # Re-check after sending the status message in case the user double-clicked.
        existing = active_jobs.get(callback.from_user.id)
        if existing and existing.state in {"queued", "running"} and not existing.cancel_requested:
            await status.edit_text("⚠️ У тебя уже есть активный запуск.")
            return
        active_jobs[job.user_id] = job
        queued_job_ids.append(job.job_id)
        position = len(queued_job_ids)
        scan_queue.put_nowait(job)

    await status.edit_text(
        "✅ <b>Задача добавлена в очередь</b>\n\n"
        f"Категорий: <b>{len(job.category_keys)}</b>\n"
        f"Позиция в очереди: примерно <b>{position}</b>\n"
        f"Одновременно работают: до <b>{MAX_CONCURRENT_JOBS}</b> задач\n"
        f"Свежий кэш категории: <b>{CATEGORY_CACHE_TTL_SECONDS // 60} мин.</b>\n\n"
        "Если другая задача уже парсит ту же категорию, твоя подключится к её результату — второй запрос к Kleinanzeigen не создаётся.",
        parse_mode=ParseMode.HTML,
        reply_markup=job_keyboard(job.job_id),
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
    try:
        await dp.start_polling(bot)
    finally:
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        async with category_inflight_guard:
            inflight = list(category_inflight.values())
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
