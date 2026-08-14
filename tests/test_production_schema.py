from models import BotUser, UserScan, UserSettings


def test_user_schema_has_onboarding_and_subscription_notice_state():
    columns = set(BotUser.__table__.columns.keys())
    assert {
        "onboarding_completed",
        "expiry_warning_sent_for",
        "expiry_expired_sent_for",
    } <= columns


def test_scan_schema_has_restart_recovery_state():
    columns = set(UserScan.__table__.columns.keys())
    assert {
        "chat_id",
        "status_message_id",
        "resumed_count",
        "retry_count",
        "last_error",
        "incomplete_category_keys",
    } <= columns


def test_user_settings_schema_has_min_views():
    columns = set(UserSettings.__table__.columns.keys())
    assert "min_views" in columns
