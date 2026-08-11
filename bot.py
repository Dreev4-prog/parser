from __future__ import annotations

import asyncio
import csv
import html
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from categories import CATEGORIES, GROUPS, categories_for_group, group_root_key
from db import SessionLocal, init_db
from models import Listing, SelectedCategory
from parser import KleinanzeigenParser, ParsedListing

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("kleinanzeigen-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
BERLIN = ZoneInfo("Europe/Berlin")
scan_lock = asyncio.Lock()


def allowed(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def main_keyboard(selected_count: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать парсинг", callback_data="start_scan")],
        [InlineKeyboardButton(text=f"🗂 Выбрать категории ({selected_count})", callback_data="groups")],
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
            row.append(InlineKeyboardButton(
                text=f"{group.icon} {group.name}{suffix}", callback_data=f"grp:{group.key}"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🧹 Очистить выбор", callback_data="clear_all")])
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_keyboard(group_key: str, selected_keys: set[str]) -> InlineKeyboardMarkup:
    group = GROUPS[group_key]
    cats = categories_for_group(group_key)
    rows = []
    for cat in cats:
        selected = cat.key in selected_keys
        marker = "✅" if selected else "▫️"
        rows.append([InlineKeyboardButton(text=f"{marker} {cat.name}", callback_data=f"cat:{cat.key}")])
    child_keys = [c.key for c in cats if not c.is_group]
    children_all = bool(child_keys) and all(k in selected_keys for k in child_keys)
    rows.append([InlineKeyboardButton(
        text=("☑️ Убрать все подкатегории" if children_all else "☑️ Выбрать все подкатегории"),
        callback_data=f"grpall:{group_key}",
    )])
    rows.append([InlineKeyboardButton(text="⬅️ К разделам", callback_data="groups")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_selected(user_id: int) -> set[str]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(SelectedCategory.category_key).where(SelectedCategory.user_id == user_id)
        )
        return {x for x in result.scalars().all() if x in CATEGORIES}


async def toggle_category(user_id: int, key: str) -> set[str]:
    cat = CATEGORIES[key]
    root_key = group_root_key(cat.group)
    async with SessionLocal() as session:
        result = await session.execute(
            select(SelectedCategory).where(
                SelectedCategory.user_id == user_id,
                SelectedCategory.category_key == key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            await session.delete(existing)
        else:
            # Avoid scanning a parent category and its children at the same time.
            if cat.is_group:
                child_keys = [c.key for c in categories_for_group(cat.group) if not c.is_group]
                if child_keys:
                    await session.execute(delete(SelectedCategory).where(
                        SelectedCategory.user_id == user_id,
                        SelectedCategory.category_key.in_(child_keys),
                    ))
            else:
                await session.execute(delete(SelectedCategory).where(
                    SelectedCategory.user_id == user_id,
                    SelectedCategory.category_key == root_key,
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
            SelectedCategory.user_id == user_id,
            SelectedCategory.category_key == group_root_key(group_key),
        ))
        if all_selected:
            await session.execute(delete(SelectedCategory).where(
                SelectedCategory.user_id == user_id,
                SelectedCategory.category_key.in_(child_keys),
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


async def upsert_items(category: str, items: list[ParsedListing]) -> tuple[int, int]:
    if not items:
        return 0, 0
    ids = [x.external_id for x in items]
    async with SessionLocal() as session:
        existing_result = await session.execute(select(Listing).where(Listing.external_id.in_(ids)))
        existing = {x.external_id: x for x in existing_result.scalars().all()}
        now = datetime.utcnow()
        new_count = 0
        for item in items:
            listing = existing.get(item.external_id)
            if listing is None:
                session.add(Listing(
                    external_id=item.external_id,
                    category=category,
                    title=item.title,
                    price_text=item.price_text,
                    price_eur=item.price_eur,
                    url=item.url,
                ))
                new_count += 1
            else:
                listing.category = category
                listing.title = item.title
                listing.price_text = item.price_text
                listing.price_eur = item.price_eur
                listing.url = item.url
                listing.last_seen_at = now
        await session.commit()
    return new_count, len(items) - new_count


def write_csv(rows: list[tuple[str, ParsedListing]]) -> Path:
    now = datetime.now(BERLIN)
    temp_dir = Path(tempfile.mkdtemp(prefix="kleinanzeigen_"))
    path = temp_dir / f"kleinanzeigen_{now:%Y-%m-%d_%H-%M}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Категория", "Название", "Цена", "Дата публикации", "Ссылка"])
        for category_name, item in rows:
            writer.writerow([
                category_name,
                item.title,
                item.price_text or "",
                item.posted_text or "Сегодня",
                item.url,
            ])
    return path


def selected_text(keys: set[str]) -> str:
    if not keys:
        return "<b>Категории пока не выбраны.</b>"
    cats = [CATEGORIES[k] for k in CATEGORIES if k in keys]
    lines = [f"<b>Выбрано категорий: {len(cats)}</b>", ""]
    shown = 0
    max_show = 60
    for group in GROUPS.values():
        group_cats = [c for c in cats if c.group == group.key]
        if not group_cats:
            continue
        lines.append(f"<b>{group.icon} {html.escape(group.name)}</b>")
        for cat in group_cats:
            if shown >= max_show:
                break
            lines.append(f"• {html.escape(cat.name)}")
            shown += 1
        lines.append("")
        if shown >= max_show:
            break
    if len(cats) > shown:
        lines.append(f"…и ещё <b>{len(cats) - shown}</b> категорий.")
    return "\n".join(lines).strip()


async def stats_text() -> str:
    async with SessionLocal() as session:
        total = (await session.execute(select(func.count(Listing.id)))).scalar_one()
    return f"<b>📊 База парсера</b>\n\nВсего сохранено объявлений: <b>{total}</b>"


dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message) -> None:
    if not allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    selected = await get_selected(message.from_user.id)
    await message.answer(
        "<b>Kleinanzeigen Parser v2.0</b>\n\n"
        "1) Выбери нужные категории.\n"
        "2) Нажми <b>Начать парсинг</b>.\n"
        "3) Я пройду страницы с сортировкой по свежести, соберу объявления с пометкой <b>Heute</b> и пришлю один CSV-файл.\n\n"
        "В файле: <b>категория, название, цена, дата и ссылка</b>.",
        reply_markup=main_keyboard(len(selected)),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    selected = await get_selected(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(
        "<b>Kleinanzeigen Parser v2.0</b>\n\nВыбери действие:",
        reply_markup=main_keyboard(len(selected)), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "groups")
async def groups(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    selected = await get_selected(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(
        "<b>🗂 Категории Kleinanzeigen</b>\n\nОткрой раздел и отметь, что нужно парсить.",
        reply_markup=groups_keyboard(selected), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("grp:"))
async def open_group(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_key = callback.data.split(":", 1)[1]
    if group_key not in GROUPS:
        await callback.answer("Раздел не найден", show_alert=True)
        return
    selected = await get_selected(callback.from_user.id)
    group = GROUPS[group_key]
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{group.icon} {html.escape(group.name)}</b>\n\n"
        "Нажимай на категории, чтобы включать/выключать их.\n"
        "Можно выбрать весь раздел целиком или отдельные подкатегории.",
        reply_markup=category_keyboard(group_key, selected), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("cat:"))
async def toggle_cat(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    if key not in CATEGORIES:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    selected = await toggle_category(callback.from_user.id, key)
    cat = CATEGORIES[key]
    await callback.answer("Выбор обновлён")
    await callback.message.edit_reply_markup(reply_markup=category_keyboard(cat.group, selected))


@dp.callback_query(F.data.startswith("grpall:"))
async def toggle_all_children(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_key = callback.data.split(":", 1)[1]
    if group_key not in GROUPS:
        await callback.answer("Раздел не найден", show_alert=True)
        return
    selected = await toggle_group_children(callback.from_user.id, group_key)
    await callback.answer("Выбор обновлён")
    await callback.message.edit_reply_markup(reply_markup=category_keyboard(group_key, selected))


@dp.callback_query(F.data == "clear_all")
async def clear_all(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await clear_selected(callback.from_user.id)
    await callback.answer("Выбор очищен")
    await callback.message.edit_reply_markup(reply_markup=groups_keyboard(set()))


@dp.callback_query(F.data == "selected")
async def selected(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    keys = await get_selected(callback.from_user.id)
    await callback.answer()
    await callback.message.answer(
        selected_text(keys), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(len(keys))
    )


@dp.callback_query(F.data == "stats")
async def stats(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    keys = await get_selected(callback.from_user.id)
    await callback.message.answer(
        await stats_text(), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(len(keys))
    )


@dp.callback_query(F.data == "start_scan")
async def start_scan(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if scan_lock.locked():
        await callback.answer("Парсинг уже идёт", show_alert=True)
        return

    selected_keys = await get_selected(callback.from_user.id)
    selected_cats = [CATEGORIES[k] for k in CATEGORIES if k in selected_keys]
    if not selected_cats:
        await callback.answer("Сначала выбери хотя бы одну категорию", show_alert=True)
        return

    await callback.answer()
    status = await callback.message.answer(
        f"⏳ <b>Начинаю парсинг за сегодня</b>\nКатегорий: <b>{len(selected_cats)}</b>",
        parse_mode=ParseMode.HTML,
    )

    async with scan_lock:
        parser = KleinanzeigenParser()
        all_rows: list[tuple[str, ParsedListing]] = []
        seen_ids: set[str] = set()
        total_pages = 0
        warnings: list[str] = []
        try:
            for idx, cat in enumerate(selected_cats, start=1):
                try:
                    items, pages, hit_limit = await parser.parse_today(cat.url)
                    total_pages += pages
                    if hit_limit:
                        warnings.append(f"{cat.name}: достигнут аварийный лимит страниц")
                    new_count, old_count = await upsert_items(cat.name, items)
                    added = 0
                    for item in items:
                        if item.external_id in seen_ids:
                            continue
                        seen_ids.add(item.external_id)
                        all_rows.append((cat.name, item))
                        added += 1
                    log.info(
                        "category=%s pages=%s today=%s added_to_file=%s new_db=%s old_db=%s",
                        cat.name, pages, len(items), added, new_count, old_count,
                    )
                    await status.edit_text(
                        "⏳ <b>Парсинг за сегодня</b>\n\n"
                        f"Категория: <b>{idx}/{len(selected_cats)}</b>\n"
                        f"Сейчас: <b>{html.escape(cat.name)}</b>\n"
                        f"Страниц просмотрено всего: <b>{total_pages}</b>\n"
                        f"Объявлений в файл: <b>{len(all_rows)}</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as exc:
                    log.exception("Parsing error category=%s", cat.name)
                    warnings.append(f"{cat.name}: {str(exc)[:120]}")

            path = write_csv(all_rows)
            caption = (
                f"✅ Готово. За сегодня собрано: {len(all_rows)} объявлений\n"
                f"Категорий: {len(selected_cats)} · страниц: {total_pages}\n"
                "Формат CSV открывается в Excel."
            )
            if warnings:
                caption += f"\n⚠️ Категорий с предупреждениями: {len(warnings)}"

            await callback.message.answer_document(
                FSInputFile(path),
                caption=caption,
                reply_markup=main_keyboard(len(selected_keys)),
            )
            if warnings:
                text = "<b>⚠️ Предупреждения</b>\n\n" + "\n".join(
                    f"• {html.escape(x)}" for x in warnings[:20]
                )
                await callback.message.answer(text, parse_mode=ParseMode.HTML)

            await status.edit_text(
                f"✅ <b>Парсинг завершён</b>\n\n"
                f"Собрано объявлений за сегодня: <b>{len(all_rows)}</b>\n"
                f"Пройдено страниц: <b>{total_pages}</b>",
                parse_mode=ParseMode.HTML,
            )
        finally:
            await parser.close()


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    await init_db()
    bot = Bot(BOT_TOKEN)
    me = await bot.get_me()
    log.info("Starting @%s", me.username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
