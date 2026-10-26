"""Telegram admin controls for daily promotion posts."""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from promo_posts import (
    BUTTON_KINDS, create_post, edit_post, get_post, latest_run, list_posts,
    next_run_text, normalize_time, post_payload, promotions_enabled,
    send_post, set_promotions_enabled,
)


class PromoInput(StatesGroup):
    value = State()


def keyboard(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=callback) for text, callback in row]
        for row in rows
    ])


def back_keyboard(post_id: int = 0):
    return keyboard([[("⬅️ К посту" if post_id else "⬅️ Автопосты",
                        f"adminpromo:post:{post_id}" if post_id else "adminpromo")]])


async def menu_payload(page: int = 0):
    posts, total = await list_posts(page)
    enabled = await promotions_enabled()
    text = (
        "<b>⏰ Автопосты</b>\n\n"
        f"Отправка: <b>{'ВКЛ' if enabled else 'ПАУЗА'}</b>\n"
        "Часовой пояс: <b>Москва (МСК, UTC+3)</b>.\n"
        "Посты повторяются ежедневно. Получатели — все пользователи бота, "
        "включая подписчиков и администраторов.\n\n"
        "Открой пост, чтобы изменить время, текст, фото или кнопки.\n"
        "Новый пост сохраняется выключенным."
    )
    rows = [[("⏸ Приостановить всё" if enabled else "▶️ Включить отправку", "adminpromo:global")],
            [("➕ Добавить пост", "adminpromo:new")]]
    for post in posts:
        rows.append([(
            f"{'🟢' if post.enabled else '⚪'} {post.time_msk} · {post.title[:40]}",
            f"adminpromo:post:{post.id}",
        )])
    nav = []
    if page:
        nav.append(("⬅️", f"adminpromo:page:{page - 1}"))
    if (page + 1) * 10 < total:
        nav.append(("➡️", f"adminpromo:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("⬅️ Админ-панель", "adminhome")])
    return text, keyboard(rows)


async def detail_payload(post_id: int):
    post = await get_post(post_id)
    if post is None:
        return "Пост удалён или не найден.", back_keyboard()
    audience = "все пользователи, включая подписчиков"
    buttons = {"parser": "Парсер + Подписка", "radar": "Radar + Подписка",
               "subscription": "Подписка", "none": "без рекламных кнопок"}[post.button_kind]
    run = await latest_run(post_id)
    text = (
        f"<b>⏰ {html.escape(post.title)}</b>\n\n"
        f"Статус: <b>{'включён' if post.enabled else 'выключен'}</b>\n"
        f"Ежедневно: <b>{post.time_msk} МСК</b>\n"
        f"Следующий запуск: <b>{next_run_text(post, last_day=run.day_msk if run else '')}</b>\n"
        f"Аудитория: <b>{audience}</b>\n"
        f"Фото: <b>{'есть' if post.photo else 'нет'}</b>\n"
        f"Кнопки: <b>{buttons}</b>\n\n"
        "«Предпросмотр» отправит пост только тебе.\n"
        "Текст и фото редактируются для следующих запусков; текущая рассылка "
        "использует версию поста на момент начала.\n"
        "После изменения времени прошедший сегодня час не догоняется."
    )
    if run:
        statuses = {"done": "завершена", "running": "в работе / ожидает", "error": "ошибка",
                    "expired": "окно отправки истекло", "cancelled": "отменена"}
        uncertain = max(0, run.processed - run.sent - run.skipped - run.blocked - run.failed)
        text += (
            f"\n\nПоследняя рассылка: <b>{run.day_msk}</b> · {statuses.get(run.status, run.status)}\n"
            f"Доставлено: <b>{run.sent}</b> · пропущено: <b>{run.skipped}</b>\n"
            f"Чат недоступен: <b>{run.blocked}</b> · ошибок: <b>{run.failed}</b>"
        )
        if uncertain:
            text += f"\nБез подтверждения после обрыва: <b>{uncertain}</b> (повторно не отправляются)."
        if run.last_error:
            text += f"\nПричина: <code>{html.escape(run.last_error)}</code>"
    prefix = f"adminpromo:edit:{post.id}:"
    return text, keyboard([
        [("⏸ Выключить" if post.enabled else "▶️ Включить", f"adminpromo:toggle:{post.id}")],
        [("👁 Предпросмотр", f"adminpromo:preview:{post.id}"), ("🕐 Время", prefix + "time")],
        [("✏️ Текст", prefix + "text"), ("🖼 Картинка", prefix + "photo")],
        [("🏷 Название", prefix + "title"), ("🔘 Кнопки", f"adminpromo:buttons:{post.id}")],
        [("🗑 Удалить пост", f"adminpromo:delete:{post.id}")],
        [("⬅️ Автопосты", "adminpromo")],
    ])


def _caption_value(message: Message, *, photo_caption: bool = False) -> str:
    plain = (message.caption if photo_caption else message.text) or ""
    # Telegram's caption limit is 1024 UTF-16 units after entity parsing.
    if len(plain.encode("utf-16-le")) // 2 > 1024:
        raise ValueError("Подпись должна быть не длиннее 1024 символов (эмодзи могут занимать два).")
    # aiogram's html_text property handles text AND photo captions.
    return message.html_text or ""


def build_promo_router(admin_ids: set[int], edit_or_answer) -> Router:
    router = Router(name="scheduled-promotions")

    @router.callback_query(F.data.in_({"promo_mute", "promo_unmute"}))
    async def preference(callback: CallbackQuery):
        # Stale buttons on messages sent by 4.23.26 must not recreate opt-outs.
        await callback.answer("Расписанием публикаций управляет администратор.", show_alert=True)

    @router.callback_query(F.data.startswith("adminpromo"))
    async def controls(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in admin_ids:
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        await state.clear()
        parts = str(callback.data).split(":")
        action = parts[1] if len(parts) > 1 else "menu"
        if action in {"menu", "page", "global"}:
            if action == "global":
                await set_promotions_enabled(not await promotions_enabled())
            page = max(0, int(parts[2])) if action == "page" else 0
            text, markup = await menu_payload(page)
        elif action == "new":
            await state.set_state(PromoInput.value)
            await state.update_data(promo_field="new_title")
            await callback.message.answer("🏷 Напиши название нового поста для админ-панели (до 100 символов).",
                                          reply_markup=back_keyboard())
            return
        else:
            try:
                post_id = int(parts[2])
            except (IndexError, ValueError):
                return
            post = await get_post(post_id)
            if not post:
                text, markup = "Пост удалён или не найден.", back_keyboard()
            elif action == "preview":
                try:
                    await send_post(callback.bot, callback.from_user.id, post_payload(post), preview=True)
                    await callback.message.answer("Это предпросмотр. Пользователям ничего не отправлено.",
                                                  reply_markup=back_keyboard(post_id))
                except (TelegramBadRequest, TelegramForbiddenError, FileNotFoundError, ValueError) as exc:
                    await callback.message.answer("⚠️ Предпросмотр не отправлен: " + html.escape(str(exc))[:700],
                                                  parse_mode="HTML", reply_markup=back_keyboard(post_id))
                return
            elif action == "toggle":
                await edit_post(post_id, enabled=not post.enabled)
                text, markup = await detail_payload(post_id)
            elif action in {"audience", "setaudience"}:
                # Compatibility with old admin messages: audience is now fixed.
                text, markup = await detail_payload(post_id)
            elif action == "buttons":
                text = "🔘 Выбери кнопки под постом."
                markup = keyboard([
                    [(label, f"adminpromo:setbuttons:{post_id}:{kind}")]
                    for label, kind in (("🚀 Парсер + Подписка", "parser"), ("📡 Radar + Подписка", "radar"),
                                        ("💎 Только подписка", "subscription"), ("Без рекламных кнопок", "none"))
                ] + [[("⬅️ К посту", f"adminpromo:post:{post_id}")]])
            elif action == "setbuttons" and parts[3] in BUTTON_KINDS:
                await edit_post(post_id, button_kind=parts[3])
                text, markup = await detail_payload(post_id)
            elif action == "delete":
                text = f"Удалить пост «{html.escape(post.title)}» из расписания? Новые отправки прекратятся."
                markup = keyboard([
                    [("🗑 Да, удалить", f"adminpromo:confirmdelete:{post_id}")],
                    [("⬅️ Отмена", f"adminpromo:post:{post_id}")],
                ])
            elif action == "confirmdelete":
                await edit_post(post_id, enabled=False, deleted=True)
                text, markup = await menu_payload()
            elif action == "removephoto":
                await edit_post(post_id, photo="")
                text, markup = await detail_payload(post_id)
            elif action == "edit":
                field = parts[3]
                prompts = {
                    "title": "🏷 Напиши новое название поста (до 100 символов).",
                    "time": "🕐 Напиши время ежедневной отправки по Москве, например 17:00.",
                    "text": "✏️ Пришли новый текст поста (до 1024 символов). Форматирование Telegram сохраняется. Картинка останется прежней.",
                    "photo": "🖼 Пришли новую картинку как фотографию. Текст поста останется прежним.",
                }
                if field not in prompts:
                    return
                await state.set_state(PromoInput.value)
                await state.update_data(promo_field=field, promo_post_id=post_id)
                markup = back_keyboard(post_id)
                if field == "photo":
                    markup = keyboard([
                        [("Убрать картинку", f"adminpromo:removephoto:{post_id}")],
                        [("⬅️ Отмена", f"adminpromo:post:{post_id}")],
                    ])
                await callback.message.answer(prompts[field], reply_markup=markup)
                return
            else:
                text, markup = await detail_payload(post_id)
        await edit_or_answer(callback.message, text, reply_markup=markup)

    @router.message(PromoInput.value)
    async def input_value(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids:
            await state.clear()
            return
        data = await state.get_data()
        field = data.get("promo_field", "")
        post_id = int(data.get("promo_post_id") or 0)
        try:
            if field in {"new_title", "title"}:
                value = (message.text or "").strip()
                if not value or len(value) > 100:
                    raise ValueError("Напиши название длиной от 1 до 100 символов.")
                if field == "new_title":
                    await state.update_data(promo_field="new_content", promo_draft={"title": value})
                    await message.answer("Пришли готовый пост: текст или фото с подписью до 1024 символов. "
                                         "Он сохранится выключенным.", reply_markup=back_keyboard())
                    return
                await edit_post(post_id, title=value)
            elif field == "new_content":
                if message.media_group_id:
                    raise ValueError("Отправь одну картинку, без альбома.")
                if not message.photo and not message.text:
                    raise ValueError("Поддерживается текст или одна фотография с подписью.")
                caption = _caption_value(message, photo_caption=bool(message.photo))
                draft = dict(data.get("promo_draft") or {})
                draft.update(caption=caption, photo=message.photo[-1].file_id if message.photo else "")
                await state.update_data(promo_field="new_time", promo_draft=draft)
                await message.answer("🕐 Во сколько отправлять этот пост ежедневно? Напиши ЧЧ:ММ по Москве, например 12:00.",
                                     reply_markup=back_keyboard())
                return
            elif field in {"new_time", "time"}:
                value = normalize_time(message.text or "")
                if field == "new_time":
                    draft = dict(data.get("promo_draft") or {})
                    post = await create_post(**draft, time_msk=value)
                    post_id = post.id
                else:
                    await edit_post(post_id, time_msk=value)
            elif field == "text":
                if not message.text:
                    raise ValueError("Пришли текст; картинка меняется отдельной кнопкой.")
                await edit_post(post_id, caption=_caption_value(message))
            elif field == "photo":
                if not message.photo or message.media_group_id:
                    raise ValueError("Пришли одну картинку как фотографию, без альбома.")
                await edit_post(post_id, photo=message.photo[-1].file_id)
            else:
                await state.clear()
                return
        except ValueError as exc:
            await message.answer(str(exc), reply_markup=back_keyboard(post_id))
            return
        await state.clear()
        text, markup = await detail_payload(post_id)
        await message.answer("✅ Сохранено.\n\n" + text, parse_mode="HTML", reply_markup=markup)

    return router
