import asyncio
import html
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from categories import CATEGORIES
from db import SessionLocal, init_db
from models import Listing
from parser import KleinanzeigenParser

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("kleinanzeigen-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_SHOW = int(os.getenv("MAX_SHOW", "10"))
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

scan_locks: dict[str, asyncio.Lock] = {key: asyncio.Lock() for key in CATEGORIES}


def allowed(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def category_keyboard() -> InlineKeyboardMarkup:
    rows = []
    items = list(CATEGORIES.items())
    for i in range(0, len(items), 2):
        row = []
        for key, data in items[i : i + 2]:
            row.append(InlineKeyboardButton(text=data["name"], callback_data=f"scan:{key}"))
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="🗂 Последние", callback_data="latest"),
        InlineKeyboardButton(text="📊 База", callback_data="stats"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def upsert_items(category: str, items) -> tuple[list[Listing], int]:
    new_listings: list[Listing] = []
    updated = 0
    async with SessionLocal() as session:
        for item in items:
            result = await session.execute(
                select(Listing).where(Listing.external_id == item.external_id)
            )
            listing = result.scalar_one_or_none()
            if listing is None:
                listing = Listing(
                    external_id=item.external_id,
                    category=category,
                    title=item.title,
                    price_text=item.price_text,
                    price_eur=item.price_eur,
                    url=item.url,
                )
                session.add(listing)
                new_listings.append(listing)
            else:
                listing.category = category
                listing.title = item.title
                listing.price_text = item.price_text
                listing.price_eur = item.price_eur
                listing.url = item.url
                listing.last_seen_at = datetime.utcnow()
                updated += 1
        await session.commit()
    return new_listings, updated


def format_listings(items: list[Listing], heading: str) -> str:
    if not items:
        return f"<b>{html.escape(heading)}</b>\n\nНовых объявлений нет."

    lines = [f"<b>{html.escape(heading)}</b>", ""]
    for i, item in enumerate(items[:MAX_SHOW], start=1):
        title = html.escape(item.title)
        price = html.escape(item.price_text or "Цена не указана")
        url = html.escape(item.url, quote=True)
        lines.append(f'{i}. <a href="{url}">{title}</a> — <b>{price}</b>')
    if len(items) > MAX_SHOW:
        lines.append("")
        lines.append(f"Ещё новых: {len(items) - MAX_SHOW}")
    return "\n".join(lines)


async def latest_text(limit: int = 15) -> str:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing).order_by(Listing.first_seen_at.desc()).limit(limit)
        )
        items = list(result.scalars().all())
    return format_listings(items, f"Последние {len(items)} объявлений")


async def stats_text() -> str:
    async with SessionLocal() as session:
        total = (await session.execute(select(func.count(Listing.id)))).scalar_one()
        result = await session.execute(
            select(Listing.category, func.count(Listing.id))
            .group_by(Listing.category)
            .order_by(func.count(Listing.id).desc())
        )
        rows = result.all()

    text = ["<b>📊 База парсера</b>", "", f"Всего объявлений: <b>{total}</b>"]
    for category, count in rows:
        text.append(f"• {html.escape(category)}: <b>{count}</b>")
    return "\n".join(text)


dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message) -> None:
    if not allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer(
        "<b>Kleinanzeigen Parser</b>\n\n"
        "Выбери категорию. Я соберу первую страницу свежих объявлений и сохраню их в базу.\n\n"
        "Сейчас сохраняю: <b>название, цену и ссылку</b>.",
        reply_markup=category_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("scan:"))
async def scan_category(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    key = callback.data.split(":", 1)[1]
    cfg = CATEGORIES.get(key)
    if not cfg:
        await callback.answer("Категория не найдена", show_alert=True)
        return

    lock = scan_locks[key]
    if lock.locked():
        await callback.answer("Эта категория уже парсится", show_alert=True)
        return

    await callback.answer()
    status = await callback.message.answer(f"⏳ Собираю {cfg['name']}…")

    async with lock:
        parser = KleinanzeigenParser()
        try:
            items = await parser.parse_category_page(cfg["url"])
            new_items, updated = await upsert_items(cfg["db_name"], items)
            log.info(
                "Category=%s found=%s new=%s updated=%s",
                cfg["db_name"], len(items), len(new_items), updated,
            )
            await status.edit_text(
                f"✅ <b>{html.escape(cfg['db_name'])}</b>\n"
                f"Найдено: <b>{len(items)}</b>\n"
                f"Новых: <b>{len(new_items)}</b>\n"
                f"Уже были в базе: <b>{updated}</b>",
                parse_mode=ParseMode.HTML,
            )
            await callback.message.answer(
                format_listings(new_items, f"Новые — {cfg['db_name']}"),
                parse_mode=ParseMode.HTML,
                reply_markup=category_keyboard(),
            )
        except Exception as exc:
            log.exception("Parsing error")
            await status.edit_text(
                "❌ Ошибка парсинга. Посмотри Railway Logs.\n"
                f"<code>{html.escape(str(exc)[:500])}</code>",
                parse_mode=ParseMode.HTML,
            )
        finally:
            await parser.close()


@dp.callback_query(F.data == "latest")
async def latest(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        await latest_text(), parse_mode=ParseMode.HTML, reply_markup=category_keyboard()
    )


@dp.callback_query(F.data == "stats")
async def stats(callback: CallbackQuery) -> None:
    if not allowed(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        await stats_text(), parse_mode=ParseMode.HTML, reply_markup=category_keyboard()
    )


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
