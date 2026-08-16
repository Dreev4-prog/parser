from sqlalchemy import BigInteger

from models import (
    BotUser,
    ParserRun,
    SelectedCategory,
    SubscriptionPayment,
    UserScan,
    UserSettings,
)


def _type(model, column):
    return model.__table__.c[column].type


def test_all_persisted_telegram_user_ids_are_bigint():
    for model in (
        BotUser,
        SelectedCategory,
        UserSettings,
        ParserRun,
        UserScan,
        SubscriptionPayment,
    ):
        assert isinstance(_type(model, "user_id"), BigInteger), model.__tablename__


def test_large_telegram_id_fits_model_values():
    # Representative modern Telegram ID above signed 32-bit INTEGER max.
    uid = 7_123_456_789
    assert BotUser(user_id=uid).user_id == uid
    assert UserSettings(user_id=uid).user_id == uid
    assert SelectedCategory(user_id=uid, category_key="test").user_id == uid
