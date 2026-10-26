import asyncio
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import Message, Update

import promo_admin
import promo_posts as promo
from i18n import strip_protected
from test_promo_posts import database


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        values = {
            "message_id": len(self.requests), "date": datetime.now(timezone.utc),
            "chat": {"id": method.chat_id, "type": "private"},
            "text": getattr(method, "text", None),
        }
        if getattr(method, "caption", None):
            values["caption"] = method.caption
        if method.__api_method__ == "sendPhoto":
            values["photo"] = [{"file_id": "preview-id", "file_unique_id": "p", "width": 100, "height": 100}]
        return Message.model_validate(values).as_(bot)


def test_admin_can_create_preview_edit_schedule_and_delete_without_mass_send(monkeypatch):
    async def scenario():
        async with database(monkeypatch):
            await promo.initialize_promotions()
            session = RecordingSession()
            bot = Bot("123:TEST", session=session)
            dp = Dispatcher()
            async def edit(message, text, reply_markup=None):
                await message.answer(text, reply_markup=reply_markup)
            dp.include_router(promo_admin.build_promo_router({99}, edit))
            uid = 99
            seq = 0

            async def event(*, text=None, data=None, photo=False, caption=None, user_id=None):
                nonlocal seq
                seq += 1
                who = user_id or uid
                user = {"id": who, "is_bot": False, "first_name": "Admin"}
                message = {"message_id": seq, "date": datetime.now(timezone.utc),
                           "chat": {"id": who, "type": "private"}, "from": user}
                if text is not None:
                    message["text"] = text
                if photo:
                    message["photo"] = [{"file_id": "custom-photo", "file_unique_id": "custom", "width": 200, "height": 200}]
                    message["caption"] = caption
                if data is not None:
                    message["text"] = "Панель"
                    update = {"update_id": seq, "callback_query": {
                        "id": str(seq), "from": user, "chat_instance": "instance", "data": data,
                        "message": message}}
                else:
                    update = {"update_id": seq, "message": message}
                await dp.feed_update(bot, Update.model_validate(update))

            await event(data="adminpromo:new")
            await event(text="Мой новый пост")
            await event(photo=True, caption="Моя подпись")
            await event(text="18:45")
            posts, total = await promo.list_posts()
            custom = next(p for p in posts if p.title == "Мой новый пост")
            assert total == 3 and not custom.enabled and custom.audience == "all"
            assert custom.photo == "custom-photo" and custom.caption == "Моя подпись"
            assert custom.time_msk == "18:45"
            await event(data=f"adminpromo:preview:{custom.id}")
            assert any(r.__api_method__ == "sendPhoto" for r in session.requests)
            assert all(getattr(r, "chat_id", 99) == 99 for r in session.requests)
            await event(data=f"adminpromo:edit:{custom.id}:time")
            await event(text="не время")
            assert (await promo.get_post(custom.id)).time_msk == "18:45"
            await event(text="19:15")
            await event(data=f"adminpromo:edit:{custom.id}:text")
            await event(text="Новая подпись")
            await event(data=f"adminpromo:edit:{custom.id}:text_en")
            await event(text="Discover today's listings")
            await event(data=f"adminpromo:edit:{custom.id}:photo_en")
            await event(photo=True)
            await event(data=f"adminpromo:preview_en:{custom.id}")
            assert any(
                strip_protected(getattr(request, "caption", "") or "") == "Discover today's listings"
                for request in session.requests)
            assert (await promo.get_post(custom.id)).caption_en == "Discover today's listings"
            await event(data=f"adminpromo:setbuttons:{custom.id}:radar")
            # An old admin keyboard cannot restore the subscriber-only filter.
            await event(data=f"adminpromo:setaudience:{custom.id}:unsubscribed")
            await event(data=f"adminpromo:toggle:{custom.id}")
            saved = await promo.get_post(custom.id)
            assert saved.enabled and saved.time_msk == "19:15"
            assert saved.caption == "Новая подпись" and saved.photo == "custom-photo"
            assert saved.button_kind == "radar" and saved.audience == "all"
            await event(data="promo_mute", user_id=7)
            assert "управляет администратор" in session.requests[-1].text
            await event(data="adminpromo:global")
            assert not await promo.promotions_enabled()
            await event(data=f"adminpromo:delete:{custom.id}")
            assert await promo.get_post(custom.id) is not None
            await event(data=f"adminpromo:confirmdelete:{custom.id}")
            assert await promo.get_post(custom.id) is None
            # A normal user cannot toggle the scheduler via a crafted callback.
            await event(data="adminpromo:global", user_id=7)
            assert not await promo.promotions_enabled()
            assert "Нет доступа" in session.requests[-1].text
            await dp.storage.close()
            await bot.session.close()
    asyncio.run(scenario())
