"""Records budget filter: SQL boundaries, isolated UI state and navigation."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot as ui
import radar
from i18n import translate_text
from models import Base, Listing, RadarProduct, RadarProductListing
from test_promo_admin import RecordingSession


def test_records_query_filters_real_prices_inclusively_before_count_and_pagination(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(radar, "SessionLocal", factory)
        now = datetime.utcnow()
        # Old historical records remain eligible. A wide family envelope alone
        # must not match the selected range without an observed listing in it.
        prices = {1:[99], 2:[100], 3:[250], 4:[500], 5:[501], 6:[None],
                  7:[50,700], 8:[50,300,700]}
        try:
            async with factory() as session:
                for product_id, values in prices.items():
                    known = [value for value in values if value is not None]
                    session.add(RadarProduct(id=product_id,product_key=f"records-{product_id}",
                        title=f"Record {product_id}",category_key="hg_kueche",status="historical",
                        organic_verified_at=now,last_signal_at=now-timedelta(days=90),
                        peak_score=100-product_id,current_score=50,
                        min_price_eur=min(known) if known else None,
                        max_price_eur=max(known) if known else None))
                    for index, price in enumerate(values):
                        external_id=f"{product_id}-{index}"
                        session.add(Listing(external_id=external_id,category="Küche & Esszimmer",
                            title="Example",url=f"https://example.test/{external_id}",price_eur=price,
                            is_active=False,is_promoted=False,is_price_reduced=False))
                        session.add(RadarProductListing(product_id=product_id,external_id=external_id,last_price_eur=price))
                await session.commit()
            rows,total = await radar.list_radar_products(mode="alltime",price_filter="custom:100:500",page_size=2)
            assert [r.id for r in rows] == [2,3] and total == 4
            rows,total = await radar.list_radar_products(mode="alltime",price_filter="custom:100:500",page_size=2,page=1)
            assert [r.id for r in rows] == [4,8] and total == 4
            assert all(r.status == "historical" for r in rows)
            for price_filter, expected in (("custom::100",[1,2,7,8]),
                                            ("custom:500:",[4,5,7,8]),
                                            ("custom:100:100",[2]),
                                            ("custom:900:1000",[]),
                                            ("any",list(prices))):
                rows,total=await radar.list_radar_products(mode="alltime",price_filter=price_filter,page_size=20)
                assert [r.id for r in rows] == expected and total == len(expected)
        finally:
            await engine.dispose()
    asyncio.run(scenario())


def test_records_price_journey_preserves_budget_through_pages_and_product_back(monkeypatch):
    async def scenario():
        monkeypatch.setattr(ui,"allowed",lambda user_id:True)
        now=datetime.utcnow()
        product=NS(id=1,title="Coffee machine",status="historical",category_key="hg_kueche",
            current_score=50,peak_score=90,min_price_eur=150,max_price_eur=150,
            last_signal_at=now,first_radar_at=now,signal_count=4,listing_count=1)
        query=AsyncMock(side_effect=lambda **kwargs: ([],0) if kwargs["price_filter"]=="custom:900:1000" else ([product]*12,30))
        monkeypatch.setattr(ui,"list_radar_products",query)
        card=AsyncMock(return_value=("Product card",ui.radar_product_keyboard(1)))
        monkeypatch.setattr(ui,"_radar_product_payload",card)
        session=RecordingSession(); client=Bot("123:TEST",session=session); dp=Dispatcher()
        for prefix, handler in (("radarlist:",ui.radar_list_handler),
                                ("radarprice:set:",ui.radar_price_set),
                                ("radaritem:",ui.radar_item_handler)):
            dp.callback_query.register(handler,F.data.startswith(prefix))
        for value, handler in (("radarprice:open",ui.radar_price_open),
                               ("radarprice:custom",ui.radar_price_custom),
                               ("radarprice:back",ui.radar_price_back)):
            dp.callback_query.register(handler,F.data==value)
        dp.message.register(ui.radar_price_custom_message,ui.RadarInput.price)
        state=FSMContext(dp.storage,StorageKey(bot_id=client.id,chat_id=1,user_id=1))
        await state.update_data(radar_price_filter="0_50")
        counter=0
        async def event(data=None,text=None):
            nonlocal counter
            counter+=1
            user={"id":1,"is_bot":False,"first_name":"User"}
            message={"message_id":counter,"date":datetime.now(timezone.utc),
                "chat":{"id":1,"type":"private"},"from":user,"text":text or "Radar"}
            payload={"update_id":counter}
            if data:
                payload["callback_query"]={"id":str(counter),"from":user,"chat_instance":"test",
                    "data":data,"message":message}
            else:
                payload["message"]=message
            await dp.feed_update(client,Update.model_validate(payload))
        try:
            await event("radarlist:alltime:0")
            assert query.await_args.kwargs["price_filter"] == "any"
            assert any(b.callback_data=="radarprice:open" for row in session.requests[-1].reply_markup.inline_keyboard for b in row)
            await event("radarprice:open")
            assert "Фильтр цены" in session.requests[-1].text
            await event("radarprice:custom")
            assert await state.get_state() == ui.RadarInput.price.state
            await event(text="500-100")
            assert await state.get_state() == ui.RadarInput.price.state
            assert "Не понял диапазон" in session.requests[-1].text
            await event(text="от 100 до 500")
            assert await state.get_state() is None
            assert query.await_args.kwargs["price_filter"] == "custom:100:500"
            assert query.await_args.kwargs["page"] == 0
            assert "100–500 €" in session.requests[-1].text
            assert (await state.get_data())["radar_price_filter"] == "0_50"
            await event("radarlist:alltime:1")
            assert query.await_args.kwargs["page"] == 1
            assert query.await_args.kwargs["price_filter"] == "custom:100:500"
            await event("radaritem:1")
            assert card.await_args.kwargs["return_callback"] == "radarlist:alltime:1"
            await event("radarlist:alltime:1")
            assert query.await_args.kwargs["price_filter"] == "custom:100:500"
            await event("radarprice:open")
            await event("radarprice:back")
            assert query.await_args.kwargs["page"] == 1
            await event("radarprice:set:200_500")
            assert query.await_args.kwargs["page"] == 0
            assert query.await_args.kwargs["price_filter"] == "200_500"
            await event("radarprice:custom")
            await event(text="900-1000")
            empty=session.requests[-1].text
            assert "В этом диапазоне цен товаров нет" in empty
            assert "No products in this price range" in translate_text(empty,"en")
            await event("radarprice:set:any")
            assert query.await_args.kwargs["price_filter"] == "any"
            await event("radarlist:hot48:0")
            assert query.await_args.kwargs["mode"] == "alltime"
        finally:
            await dp.storage.close(); await client.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,mode,key",[("category","category_new","radar_price_filter"),
    ("search","","radar_price_filter"),("list","alltime","radar_records_price_filter"),
    ("list","hot48","radar_records_price_filter")])
def test_records_budget_is_separate_from_existing_category_and_search_filter(kind,mode,key):
    data={"radar_context_kind":kind,"radar_context_mode":mode,"radar_price_filter":"0_50",
          "radar_records_price_filter":"custom:100:500"}
    assert ui._radar_price_filter(data) == data[key]


def test_expired_access_cannot_submit_a_pending_records_price(monkeypatch):
    async def scenario():
        monkeypatch.setattr(ui,"allowed",lambda user_id:False)
        query=AsyncMock(); monkeypatch.setattr(ui,"list_radar_products",query)
        state=NS(set_state=AsyncMock())
        message=NS(from_user=NS(id=1),text="100-500",answer=AsyncMock())
        await ui.radar_price_custom_message(message,state)
        query.assert_not_awaited(); state.set_state.assert_awaited_once_with(None)
        assert "Доступно в полном DT Radar" in message.answer.await_args.args[0]
    asyncio.run(scenario())
