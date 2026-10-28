"""Historical Records categories compose with price and preserve navigation."""
import asyncio
import re
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Update

import bot as ui
from i18n import translate_text
from test_promo_admin import RecordingSession


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_category_and_price_journey_stays_in_historical_records(monkeypatch):
    async def scenario():
        monkeypatch.setattr(ui,"allowed",lambda user_id:True)
        now=datetime.utcnow()
        product=NS(id=1,title="Coffee machine",status="historical",category_key="hg_kueche",
            current_score=50,peak_score=90,min_price_eur=150,max_price_eur=150,
            last_signal_at=now,first_radar_at=now,signal_count=4,listing_count=1)
        query=AsyncMock(return_value=([product]*12,30))
        monkeypatch.setattr(ui,"list_radar_products",query)
        card=AsyncMock(return_value=("Card",ui.radar_product_keyboard(1)))
        monkeypatch.setattr(ui,"_radar_product_payload",card)
        session=RecordingSession(); client=Bot("123:TEST",session=session); dp=Dispatcher()
        for prefix, handler in (("radarlist:",ui.radar_list_handler),
            ("radarrecords:",ui.radar_records_category_handler),
            ("radarprice:set:",ui.radar_price_set),("radaritem:",ui.radar_item_handler)):
            dp.callback_query.register(handler,F.data.startswith(prefix))
        dp.callback_query.register(ui.radar_price_custom,F.data=="radarprice:custom")
        dp.callback_query.register(ui.radar_price_back,F.data=="radarprice:back")
        dp.message.register(ui.radar_price_custom_message,ui.RadarInput.price)
        state=FSMContext(dp.storage,StorageKey(bot_id=client.id,chat_id=1,user_id=1))
        await state.update_data(radar_price_filter="0_50",radar_records_price_filter="custom:100:500")
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
            assert query.await_args.kwargs["category_key"] is None
            assert "radarrecords:categories" in callbacks(session.requests[-1].reply_markup)
            await event("radarrecords:categories")
            assert "100–500 €" in session.requests[-1].text
            assert "radarrecords:group:haus" in callbacks(session.requests[-1].reply_markup)
            await event("radarrecords:group:haus")
            choices=callbacks(session.requests[-1].reply_markup)
            assert "radarrecords:set:hg_kueche" in choices
            assert not any(value.startswith("radarcat:") for value in choices)
            assert not any(value.startswith("radarrecords:set:el_") for value in choices)
            await event("radarrecords:set:hg_kueche")
            assert query.await_args.kwargs == dict(mode="alltime",category_key="hg_kueche",page=0,
                user_id=1,price_filter="custom:100:500")
            text_value=session.requests[-1].text
            assert "Рекорды Radar" in text_value and "Küche &amp; Esszimmer" in text_value
            assert "сначала новые" not in text_value and "актуальные live-сигналы" not in text_value
            assert "radarlist:alltime:1" in callbacks(session.requests[-1].reply_markup)
            assert not any(value.startswith("radarcat:") for value in callbacks(session.requests[-1].reply_markup))
            await event("radarlist:alltime:1")
            assert query.await_args.kwargs["category_key"] == "hg_kueche"
            await event("radaritem:1")
            assert card.await_args.kwargs["return_callback"] == "radarlist:alltime:1"
            await event("radarlist:alltime:1")
            assert query.await_args.kwargs["category_key"] == "hg_kueche"
            await event("radarrecords:categories")
            assert "radarlist:alltime:1" in callbacks(session.requests[-1].reply_markup)
            await event("radarlist:alltime:1")
            assert query.await_args.kwargs["page"] == 1
            await event("radarprice:set:200_500")
            assert query.await_args.kwargs["category_key"] == "hg_kueche"
            assert query.await_args.kwargs["price_filter"] == "200_500"
            assert query.await_args.kwargs["page"] == 0
            await event("radarprice:custom")
            await event(text="120-250")
            assert query.await_args.kwargs["category_key"] == "hg_kueche"
            assert query.await_args.kwargs["price_filter"] == "custom:120:250"
            await event("radarrecords:group:elektronik")
            await event("radarrecords:set:el_konsolen")
            assert query.await_args.kwargs["category_key"] == "el_konsolen"
            assert query.await_args.kwargs["price_filter"] == "custom:120:250"
            assert (await state.get_data())["radar_price_filter"] == "0_50"
            await event("radarrecords:all")
            assert query.await_args.kwargs["category_key"] is None
            assert query.await_args.kwargs["price_filter"] == "custom:120:250"
            await event("radarrecords:set:hg_kueche")
            await event("radarprice:set:any")
            assert query.await_args.kwargs["category_key"] == "hg_kueche"
            assert query.await_args.kwargs["price_filter"] == "any"
            await event("radarlist:hot48:0")
            assert query.await_args.kwargs["category_key"] == "hg_kueche"
            assert query.await_args.kwargs["mode"] == "alltime"
            query.return_value=([],0)
            await event("radarlist:alltime:0")
            assert "По выбранной категории и цене товаров нет" in session.requests[-1].text
            assert "No products match this category and price" in translate_text(session.requests[-1].text,"en")
            assert "radarrecords:categories" in callbacks(session.requests[-1].reply_markup)
        finally:
            await dp.storage.close(); await client.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("data,access",[("radarrecords:set:missing",True),
    ("radarrecords:set:g_haus",True),("radarrecords:group:missing",True),
    ("radarrecords:set:hg_kueche:extra",True),("radarrecords:all:extra",True),
    ("radarrecords:categories",False),("radarrecords:set:hg_kueche",False)])
def test_invalid_or_unpaid_category_selection_never_changes_state_or_queries(monkeypatch,data,access):
    async def scenario():
        monkeypatch.setattr(ui,"allowed",lambda user_id:access)
        query=AsyncMock(); monkeypatch.setattr(ui,"list_radar_products",query)
        state=NS(get_data=AsyncMock(),set_state=AsyncMock(),update_data=AsyncMock())
        callback=NS(data=data,from_user=NS(id=1),answer=AsyncMock())
        await ui.radar_records_category_handler(callback,state)
        state.get_data.assert_not_awaited(); state.update_data.assert_not_awaited()
        query.assert_not_awaited()
        assert callback.answer.await_args.kwargs["show_alert"] is True
    asyncio.run(scenario())


def test_records_category_picker_has_english_labels_and_bounded_callbacks():
    token=ui._UI_LANGUAGE.set("en")
    try:
        data={"radar_context_kind":"list","radar_context_mode":"alltime","radar_context_page":2,
              "radar_records_category":"hg_kueche","radar_records_price_filter":"custom:100:500"}
        for key in (None,*ui.GROUPS):
            text_value,markup=ui.radar_records_categories_payload(data,key)
            text_value=translate_text(text_value,"en")
            assert "Kitchen &amp; Dining" in text_value and "100–500 €" in text_value
            assert re.search("[А-Яа-яЁё]",text_value) is None
            markup=ui._localize_inline_markup(markup,"en")
            for row in markup.inline_keyboard:
                for button in row:
                    assert re.search("[А-Яа-яЁё]",button.text) is None,button.text
                    assert len(button.callback_data.encode()) <= 64
            assert "radarlist:alltime:2" in callbacks(markup)
    finally:
        ui._UI_LANGUAGE.reset(token)
