from __future__ import annotations

import asyncio
import csv
import html
import logging
import os
import shutil
import tempfile
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
from models import Listing, PriceHistory, SelectedCategory, UserSettings
from parser import (
    MAX_PAGES_PER_CATEGORY,
    PAGE_DELAY_SECONDS,
    STOP_AFTER_EMPTY_TODAY_PAGES,
    STOP_AFTER_NO_NEW_PAGES,
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
scan_lock = asyncio.Lock()
AVAILABILITY_CHECK_LIMIT = max(1, int(os.getenv("AVAILABILITY_CHECK_LIMIT", "150")))
AVAILABILITY_CONCURRENCY = max(1, min(8, int(os.getenv("AVAILABILITY_CONCURRENCY", "4"))))

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
        [InlineKeyboardButton(text="📋 Что выбрано", callback_data="selected"),
         InlineKeyboardButton(text="📊 База", callback_data="stats")],
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
        "<i>v2.4 использует более строгую группировку по модели/варианту/памяти. "
        "Нажми «ℹ️ Как работают режимы», чтобы увидеть текущие критерии.</i>"
    )


async def stats_text() -> str:
    start_utc, end_utc = berlin_today_utc_bounds()
    async with SessionLocal() as session:
        total = (await session.execute(select(func.count(Listing.id)))).scalar_one()
        today = (await session.execute(select(func.count(Listing.id)).where(
            Listing.first_seen_at >= start_utc, Listing.first_seen_at < end_utc,
        ))).scalar_one()
        priced = (await session.execute(select(func.count(Listing.id)).where(
            Listing.first_seen_at >= start_utc, Listing.first_seen_at < end_utc, Listing.price_text.is_not(None),
        ))).scalar_one()
        drops = (await session.execute(select(func.count(PriceHistory.id)))).scalar_one()
    storage = "PostgreSQL / DATABASE_URL" if USING_PERSISTENT_DATABASE else "SQLite"
    warning = "" if USING_PERSISTENT_DATABASE else "\n\n⚠️ На Railway SQLite может потеряться после redeploy/restart."
    coverage = round(priced / today * 100) if today else 0
    return (
        f"<b>📊 База парсера</b>\n\n"
        f"Сегодня собрано: <b>{today}</b>\n"
        f"С ценой: <b>{priced}</b> ({coverage}%)\n"
        f"Всего сохранено: <b>{total}</b>\n"
        f"Записей истории цен: <b>{drops}</b>\n"
        f"База: <b>{storage}</b>{warning}"
    )


async def scan_one_category(parser: KleinanzeigenParser, cat) -> tuple[int, int, int, bool, str]:
    new_count = today_seen = pages_scanned = empty_today_pages = no_new_pages = 0
    for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
        items = await parser.parse_category_page(page_url(cat.url, page))
        pages_scanned = page
        if not items:
            return new_count, pages_scanned, today_seen, False, "конец выдачи"
        today_items = [item for item in items if is_today_text(item.posted_text)]
        today_seen += len(today_items)
        if today_items:
            empty_today_pages = 0
            new_items, known_count, enriched_count = await upsert_page_items(cat.key, cat.name, today_items)
            new_count += len(new_items)
            # Keep going while backfilling prices missing from older rows.
            no_new_pages = 0 if (new_items or enriched_count) else no_new_pages + 1
            log.info(
                "category=%s page=%s total=%s today=%s new=%s known=%s prices_backfilled=%s no_new_pages=%s",
                cat.name, page, len(items), len(today_items), len(new_items), known_count, enriched_count, no_new_pages,
            )
            if no_new_pages >= STOP_AFTER_NO_NEW_PAGES:
                return new_count, pages_scanned, today_seen, False, "дошли до уже собранных"
        else:
            empty_today_pages += 1
            log.info("category=%s page=%s total=%s today=0 empty_today_pages=%s", cat.name, page, len(items), empty_today_pages)
            if empty_today_pages >= STOP_AFTER_EMPTY_TODAY_PAGES:
                return new_count, pages_scanned, today_seen, False, "закончились объявления Heute"
        if PAGE_DELAY_SECONDS and page < MAX_PAGES_PER_CATEGORY:
            await asyncio.sleep(PAGE_DELAY_SECONDS)
    return new_count, pages_scanned, today_seen, True, "аварийный лимит страниц"


dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    selected = await get_selected(message.from_user.id)
    await message.answer(
        "<b>Kleinanzeigen Parser v2.4 · Smart Analytics</b>\n\n"
        "Сначала парсер собирает объявления в базу, затем ты можешь менять режим выдачи без повторного парсинга.\n\n"
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
    await callback.message.edit_text("<b>Kleinanzeigen Parser v2.4</b>\n\nВыбери действие:", reply_markup=main_keyboard(len(selected)), parse_mode=ParseMode.HTML)


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
        "<b>ℹ️ Критерии Smart Analytics v2.4</b>\n\n"
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


@dp.callback_query(F.data == "start_scan")
async def start_scan(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id): await callback.answer("Нет доступа", show_alert=True); return
    if scan_lock.locked(): await callback.answer("Парсинг уже идёт", show_alert=True); return
    selected_keys = await get_selected(callback.from_user.id)
    selected_cats = [CATEGORIES[k] for k in CATEGORIES if k in selected_keys]
    if not selected_cats: await callback.answer("Сначала выбери хотя бы одну категорию", show_alert=True); return
    await callback.answer()
    status = await callback.message.answer(f"⏳ <b>Начинаю парсинг</b>\nКатегорий: <b>{len(selected_cats)}</b>", parse_mode=ParseMode.HTML)

    async with scan_lock:
        parser = KleinanzeigenParser()
        total_pages = total_new = 0
        warnings: list[str] = []
        try:
            for idx, cat in enumerate(selected_cats, start=1):
                try:
                    new_count, pages, today_seen, hit_limit, reason = await scan_one_category(parser, cat)
                    total_pages += pages; total_new += new_count
                    if hit_limit: warnings.append(f"{cat.name}: достигнут аварийный лимит страниц")
                    await status.edit_text(
                        "⏳ <b>Парсинг</b>\n\n"
                        f"Категория: <b>{idx}/{len(selected_cats)}</b>\n"
                        f"Сейчас: <b>{html.escape(cat.name)}</b>\n"
                        f"Новых в категории: <b>{new_count}</b>\n"
                        f"Новых за запуск: <b>{total_new}</b>\n"
                        f"Страниц: <b>{total_pages}</b>\n"
                        f"Остановка: <b>{html.escape(reason)}</b>", parse_mode=ParseMode.HTML,
                    )
                except Exception as exc:
                    log.exception("Parsing error category=%s", cat.name)
                    warnings.append(f"{cat.name}: {str(exc)[:120]}")
            today_count = len(await today_rows())
            await status.edit_text(
                "✅ <b>Парсинг завершён</b>\n\n"
                f"Новых объявлений: <b>{total_new}</b>\n"
                f"Всего сегодня в базе: <b>{today_count}</b>\n"
                f"Пройдено страниц: <b>{total_pages}</b>\n\n"
                "Теперь нажми <b>📦 Получить результат</b> — он сформируется по текущим настройкам.",
                parse_mode=ParseMode.HTML,
            )
            # Do not auto-send a potentially huge raw file. User chooses the output mode.
            if warnings:
                await callback.message.answer("<b>⚠️ Предупреждения</b>\n\n" + "\n".join(f"• {html.escape(x)}" for x in warnings[:20]), parse_mode=ParseMode.HTML)
        finally:
            await parser.close()


async def main() -> None:
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not set")
    await init_db()
    bot = Bot(BOT_TOKEN)
    me = await bot.get_me()
    log.info("Starting @%s", me.username)
    if not USING_PERSISTENT_DATABASE:
        log.warning("DATABASE_URL is not set. SQLite works, but on Railway its data may be lost after redeploy/restart.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
