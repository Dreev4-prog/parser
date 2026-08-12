from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
import importlib.util
import sys


def _load_commerce_without_database_driver():
    fake_db = ModuleType("db")
    fake_db.SessionLocal = object()
    fake_models = ModuleType("models")
    for name in ("AppSetting", "BotUser", "SubscriptionPayment", "SubscriptionPlan", "UserScan"):
        setattr(fake_models, name, type(name, (), {}))

    old_db = sys.modules.get("db")
    old_models = sys.modules.get("models")
    try:
        sys.modules["db"] = fake_db
        sys.modules["models"] = fake_models
        path = Path(__file__).resolve().parents[1] / "commerce.py"
        spec = importlib.util.spec_from_file_location("commerce_unit", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["commerce_unit"] = module
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        if old_db is None:
            sys.modules.pop("db", None)
        else:
            sys.modules["db"] = old_db
        if old_models is None:
            sys.modules.pop("models", None)
        else:
            sys.modules["models"] = old_models


commerce = _load_commerce_without_database_driver()


def _restore(mode, cache, banned):
    commerce._access_mode = mode
    commerce._access_cache.clear()
    commerce._access_cache.update(cache)
    commerce._banned_users.clear()
    commerce._banned_users.update(banned)


def test_admin_always_has_access():
    mode, cache, banned = commerce._access_mode, dict(commerce._access_cache), set(commerce._banned_users)
    try:
        commerce._access_mode = "admin_only"
        assert commerce.has_access(42, {42}) is True
    finally:
        _restore(mode, cache, banned)


def test_subscription_requires_unexpired_access():
    mode, cache, banned = commerce._access_mode, dict(commerce._access_cache), set(commerce._banned_users)
    try:
        commerce._access_mode = "subscription"
        commerce._access_cache.clear()
        commerce._banned_users.clear()
        assert commerce.has_access(100, set()) is False
        commerce._access_cache[100] = datetime.utcnow() + timedelta(hours=1)
        assert commerce.has_access(100, set()) is True
        commerce._access_cache[100] = datetime.utcnow() - timedelta(seconds=1)
        assert commerce.has_access(100, set()) is False
    finally:
        _restore(mode, cache, banned)


def test_ban_overrides_open_mode():
    mode, cache, banned = commerce._access_mode, dict(commerce._access_cache), set(commerce._banned_users)
    try:
        commerce._access_mode = "open"
        commerce._banned_users.add(777)
        assert commerce.has_access(777, set()) is False
        assert commerce.has_access(778, set()) is True
    finally:
        _restore(mode, cache, banned)


def test_paid_invoice_amount_and_currency_are_verified():
    payment = SimpleNamespace(amount_usdt=10.0)
    assert commerce._paid_invoice_matches(payment, {"amount": "10.00", "asset": "USDT"}) is True
    assert commerce._paid_invoice_matches(payment, {"amount": 10, "currency": "usdt"}) is True
    assert commerce._paid_invoice_matches(payment, {"amount": "9.99", "asset": "USDT"}) is False
    assert commerce._paid_invoice_matches(payment, {"amount": "10.00", "asset": "TON"}) is False


def test_access_modes_and_default_durations_are_explicit():
    assert commerce.ACCESS_MODE_DEFAULT in {"admin_only", "subscription", "open"}
    assert [spec[2] for spec in commerce.DEFAULT_PLAN_SPECS] == [1, 3, 7, 30]
