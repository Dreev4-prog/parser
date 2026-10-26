import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendPhoto
from aiogram.types import Message
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import promo_admin
import promo_posts as promo
import i18n
from models import AppSetting, BotUser, PromoPost, PromoRun


@asynccontextmanager
async def database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for table in (AppSetting.__table__, BotUser.__table__, PromoPost.__table__, PromoRun.__table__):
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(promo, "SessionLocal", factory)
    monkeypatch.setattr(i18n, "SessionLocal", factory)
    monkeypatch.setattr(i18n, "_language_cache", {})
    monkeypatch.setattr(promo, "SEND_INTERVAL_SECONDS", 0)
    try:
        yield factory
    finally:
        await engine.dispose()


class FakeBot:
    def __init__(self):
        self.messages = []
        self.hook = None

    async def send_photo(self, chat_id, **kwargs):
        if kwargs.get("caption"):
            kwargs["caption"] = i18n.strip_protected(kwargs["caption"])
        self.messages.append((chat_id, kwargs))
        if self.hook:
            await self.hook(chat_id)
        return SimpleNamespace(photo=[SimpleNamespace(file_id="telegram-cached-photo")])

    async def send_message(self, chat_id, text, **kwargs):
        text = i18n.strip_protected(text)
        self.messages.append((chat_id, {"text": text, **kwargs}))
        return SimpleNamespace(photo=None)


async def seed_due(monkeypatch, factory, clock):
    monkeypatch.setattr(promo, "utcnow", lambda: clock[0])
    await promo.initialize_promotions(clock[0])
    clock[0] = clock[0].replace(hour=9, minute=0)
    await promo.create_due_runs(clock[0])
    return await promo.claim_run("worker-one", clock[0])


def test_seed_is_once_and_admin_edits_and_deletion_survive_redeploy(monkeypatch):
    async def scenario():
        async with database(monkeypatch):
            await promo.initialize_promotions()
            rows, total = await promo.list_posts()
            assert total == 2
            assert [p.time_msk for p in rows] == ["12:00", "17:00"]
            assert all(p.enabled and p.audience == "all" and p.photo for p in rows)
            await promo.edit_post(rows[0].id, time_msk="13:25", caption="Изменённый текст")
            await promo.edit_post(rows[1].id, deleted=True, enabled=False)
            await promo.set_promotions_enabled(False)
            await promo.initialize_promotions()
            rows, total = await promo.list_posts()
            assert total == 1 and rows[0].time_msk == "13:25"
            assert rows[0].caption == "Изменённый текст"
            assert not await promo.promotions_enabled()
    asyncio.run(scenario())


def test_schedule_uses_moscow_and_does_not_catch_up_old_posts_on_deploy(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as factory:
            # 08:00 UTC = 11:00 Moscow. Local server timezone has no effect.
            start = datetime(2026, 10, 26, 8, 0)
            await promo.initialize_promotions(start)
            await promo.create_due_runs(start)
            assert await promo.claim_run("worker", start) is None
            noon = datetime(2026, 10, 26, 9, 0)
            await promo.create_due_runs(noon)
            await promo.create_due_runs(noon + timedelta(minutes=1))
            async with factory() as session:
                rows = (await session.execute(select(PromoRun))).scalars().all()
                assert len(rows) == 1 and rows[0].scheduled_at == noon
            await promo.create_due_runs(datetime(2026, 10, 26, 14, 0))
            async with factory() as session:
                assert (await session.execute(select(func.count(PromoRun.id)))).scalar_one() == 2
            # No hours-old backlog when a new installation starts after its slots.
        async with database(monkeypatch) as factory:
            late = datetime(2026, 10, 26, 16, 0)
            await promo.initialize_promotions(late)
            await promo.create_due_runs(late)
            async with factory() as session:
                assert (await session.execute(select(func.count(PromoRun.id)))).scalar_one() == 0
    asyncio.run(scenario())


def test_subscribers_admins_and_previously_muted_users_receive_photo_and_cta(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as factory:
            clock = [datetime(2026, 10, 26, 8, 0)]
            async with factory() as session:
                for user_id in range(1, 8):
                    session.add(BotUser(user_id=user_id, joined_at=clock[0]))
                await session.commit()
                await session.execute(update(BotUser).where(BotUser.user_id == 2).values(
                    access_until=clock[0] + timedelta(days=2)))
                await session.execute(update(BotUser).where(BotUser.user_id == 3).values(is_banned=True))
                await session.execute(update(BotUser).where(BotUser.user_id == 7).values(
                    joined_at=clock[0] + timedelta(hours=2)))
                await session.commit()
            # Legacy opt-outs no longer filter the all-users campaign.
            async with factory() as session:
                session.add(AppSetting(key="promo_mute:4", value="1"))
                await session.commit()
            run = await seed_due(monkeypatch, factory, clock)
            bot = FakeBot()
            await promo.process_run(bot, run, "worker-one", {5})
            assert [uid for uid, _ in bot.messages] == [1, 2, 4, 5, 6]
            first = bot.messages[0][1]
            assert first["caption"] == promo.PARSER_CAPTION
            assert first["parse_mode"] == "HTML"
            actions = [b.callback_data for row in first["reply_markup"].inline_keyboard for b in row]
            assert actions == ["start_scan", "subscription"]
            assert bot.messages[1][1]["photo"] == "telegram-cached-photo"
            assert (await promo.latest_run(run.post_id)).sent == 5
            assert await promo.claim_run("worker-two", clock[0]) is None
    asyncio.run(scenario())


def test_buying_subscription_does_not_remove_a_user_already_in_the_batch(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as factory:
            clock = [datetime(2026, 10, 26, 8, 0)]
            async with factory() as session:
                session.add_all([BotUser(user_id=i, joined_at=clock[0]) for i in (1, 2)])
                await session.commit()
            run = await seed_due(monkeypatch, factory, clock)
            bot = FakeBot()
            async def payment(_uid):
                async with factory() as session:
                    await session.execute(update(BotUser).where(BotUser.user_id == 2).values(
                        access_until=clock[0] + timedelta(days=7)))
                    await session.commit()
            bot.hook = payment
            await promo.process_run(bot, run, "worker-one", set())
            assert [uid for uid, _ in bot.messages] == [1, 2]
            last = await promo.latest_run(run.post_id)
            assert last.sent == 2 and last.skipped == 0
    asyncio.run(scenario())


def test_network_uncertainty_and_restart_do_not_replay_reserved_user(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as factory:
            clock = [datetime(2026, 10, 26, 8, 0)]
            async with factory() as session:
                session.add_all([BotUser(user_id=i, joined_at=clock[0]) for i in (1, 2)])
                await session.commit()
            run = await seed_due(monkeypatch, factory, clock)
            assert await promo.claim_run("other-worker", clock[0]) is None
            bot = FakeBot()
            async def interrupt(user_id):
                if user_id == 1:
                    await promo.set_promotions_enabled(False)
                    raise TelegramNetworkError(method=SendPhoto(chat_id=1, photo="file"), message="connection lost")
            bot.hook = interrupt
            await promo.process_run(bot, run, "worker-one", set())
            await promo.set_promotions_enabled(True)
            resumed = await promo.claim_run("worker-two", clock[0])
            assert resumed.cursor_user_id == 1
            bot.hook = None
            await promo.process_run(bot, resumed, "worker-two", set())
            assert [uid for uid, _ in bot.messages] == [1, 2]
            last = await promo.latest_run(run.post_id)
            assert last.sent == 1 and last.processed == 2
    asyncio.run(scenario())


def test_new_post_is_disabled_and_delete_cancels_current_run(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as factory:
            created = await promo.create_post(title="Новый", caption="Текст", time_msk="7:05")
            assert not created.enabled and created.time_msk == "07:05"
            clock = [datetime(2026, 10, 26, 8, 0)]
            async with factory() as session:
                session.add(BotUser(user_id=1, joined_at=clock[0]))
                await session.commit()
            run = await seed_due(monkeypatch, factory, clock)
            await promo.edit_post(run.post_id, deleted=True, enabled=False)
            bot = FakeBot()
            await promo.process_run(bot, run, "worker-one", set())
            assert not bot.messages
            assert (await promo.latest_run(run.post_id)).status == "cancelled"
    asyncio.run(scenario())


def test_admin_photo_caption_preserves_entities_and_caption_limit():
    message = Message.model_validate({
        "message_id": 1, "date": datetime.now(timezone.utc), "chat": {"id": 1, "type": "private"},
        "photo": [{"file_id": "id", "file_unique_id": "unique", "width": 10, "height": 10}],
        "caption": "Тест", "caption_entities": [{"type": "bold", "offset": 0, "length": 4}],
    })
    assert promo_admin._caption_value(message, photo_caption=True) == "<b>Тест</b>"
    message = message.model_copy(update={"caption": "😀" * 513})
    with pytest.raises(ValueError):
        promo_admin._caption_value(message, photo_caption=True)


def test_sql_eligibility_compiles_for_postgresql_and_default_assets_exist():
    sql = str(select(BotUser.user_id).where(*promo._eligible(
        "unsubscribed", datetime(2026, 10, 26), {10},
    )).compile(dialect=postgresql.dialect()))
    assert "access_until" not in sql
    assert "app_settings" not in sql
    assert "NOT IN" not in sql
    for name in ("promo_parser_alf.png", "promo_radar_alf.png"):
        assert (promo.ROOT / "assets" / name).is_file()


def test_post_and_preview_keyboards_never_include_optout():
    for kind in promo.BUTTON_KINDS:
        normal = promo.post_keyboard(kind)
        preview = promo.post_keyboard(kind, preview=True)
        for markup in (normal, preview):
            assert markup is None or all(b.callback_data not in {"promo_mute", "promo_unmute"}
                                         for row in markup.inline_keyboard for b in row)


def test_upgrade_changes_existing_audiences_without_resetting_posts(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as factory:
            await promo.initialize_promotions()
            posts, _ = await promo.list_posts()
            async with factory() as session:
                await session.execute(delete(AppSetting).where(AppSetting.key == promo.ALL_USERS_POLICY_KEY))
                await session.execute(update(PromoPost).where(PromoPost.id == posts[0].id).values(
                    audience="unsubscribed", time_msk="13:30", caption="Мой текст", enabled=False))
                await session.commit()
            await promo.initialize_promotions()
            changed = await promo.get_post(posts[0].id)
            assert changed.audience == "all" and changed.time_msk == "13:30"
            assert changed.caption == "Мой текст" and not changed.enabled
            # A frozen run payload from 4.23.26 must use the all-users rule too.
            clock = [datetime(2026, 10, 26, 8, 0)]
            async with factory() as session:
                session.add(BotUser(user_id=9, joined_at=clock[0], access_until=clock[0] + timedelta(days=2)))
                await session.commit()
            assert await promo.still_eligible(9, "unsubscribed", {9})
    asyncio.run(scenario())
