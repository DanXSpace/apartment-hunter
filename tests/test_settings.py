import pytest

from apartment_hunter.settings import BotSettings, SettingsError, load_settings


def test_defaults():
    assert load_settings({"DISCORD_TOKEN": "t"}) == BotSettings(token="t")


def test_missing_token_is_a_plain_error():
    with pytest.raises(SettingsError, match="DISCORD_TOKEN"):
        load_settings({})


def test_interval_below_ten_minutes_is_raised_to_ten():
    assert load_settings({"DISCORD_TOKEN": "t", "INTERVAL_MINUTES": "5"}).interval_minutes == 10


def test_parses_ids_and_the_allowlist():
    s = load_settings({"DISCORD_TOKEN": "t", "OWNER_USER_ID": "123", "ALLOWED_SERVERS": "1, 2"})
    assert (s.owner_id, s.allowed_servers) == (123, frozenset({1, 2}))


def test_a_non_numeric_value_names_the_variable():
    with pytest.raises(SettingsError, match="MAX_SEARCHES_TOTAL"):
        load_settings({"DISCORD_TOKEN": "t", "MAX_SEARCHES_TOTAL": "lots"})


def test_an_unknown_log_level_names_the_variable():
    with pytest.raises(SettingsError, match="LOG_LEVEL"):
        load_settings({"DISCORD_TOKEN": "t", "LOG_LEVEL": "loud"})
