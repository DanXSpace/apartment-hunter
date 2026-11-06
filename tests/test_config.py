import pytest

from apartment_hunter.config import ConfigError, load_settings

URL = "https://www.apartments.com/philadelphia-pa/"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("DISCORD_WEBHOOK_URL", "BROWSER_HEADLESS", "BROWSER_EXECUTABLE", "BROWSER_MODE",
                "BROWSER_EXTRA_ARGS", "DATABASE_PATH"):
        monkeypatch.delenv(var, raising=False)


def write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_quick_url_uses_env_webhook(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hook")
    s = load_settings(tmp_path / "missing.toml", quick_url=URL)
    assert [(x.name, x.url, x.webhook_url) for x in s.searches] == [("default", URL, "https://hook")]


def test_multiple_searches_and_per_search_webhook(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBURB_HOOK", "https://suburb-hook")
    cfg = write(tmp_path, f'''
webhook_url = "https://default-hook"
[[search]]
name = "Downtown"
url = "{URL}"
[[search]]
name = "Suburbs"
url = "{URL}"
interval_minutes = 30
webhook_url = "${{SUBURB_HOOK}}"
''')
    s = load_settings(cfg)
    assert [(x.name, x.interval_minutes, x.webhook_url) for x in s.searches] == [
        ("Downtown", 15, "https://default-hook"),
        ("Suburbs", 30, "https://suburb-hook"),
    ]


def test_interval_floor(tmp_path):
    cfg = write(tmp_path, f'webhook_url="https://h"\n[[search]]\nname="a"\nurl="{URL}"\n'
                          'interval_minutes=1\n')
    assert load_settings(cfg).searches[0].interval_minutes == 10


@pytest.mark.parametrize("body,match", [
    (f'[[search]]\nname="a"\nurl="{URL}"\n', "DISCORD_WEBHOOK_URL"),
    ('webhook_url="https://h"\n[[search]]\nname="a"\nurl="https://zillow.com/x"\n', "apartments.com"),
    (f'webhook_url="https://h"\n[[search]]\nname="a"\nurl="{URL}"\n[[search]]\nname="a"\nurl="{URL}"\n',
     "duplicate"),
    ('webhook_url="${NOPE_NOT_SET}"\n[[search]]\nname="a"\nurl="' + URL + '"\n', "NOPE_NOT_SET"),
    ('webhook_url="https://h"\n', "No searches"),
    ('webhook_url=""\n[[search]]\nname="a"\nurl="' + URL + '"\n', "DISCORD_WEBHOOK_URL"),
    ('webhook_url="not-a-url"\n[[search]]\nname="a"\nurl="' + URL + '"\n', "https://"),
])
def test_config_errors(tmp_path, body, match):
    with pytest.raises(ConfigError, match=match):
        load_settings(write(tmp_path, body))


def test_env_overrides_browser(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hook")
    monkeypatch.setenv("BROWSER_HEADLESS", "true")
    monkeypatch.setenv("BROWSER_MODE", "stealth")
    monkeypatch.setenv("BROWSER_EXTRA_ARGS", "--no-sandbox --disable-dev-shm-usage")
    s = load_settings(None, quick_url=URL)
    assert s.browser.headless and s.browser.mode == "stealth"
    assert s.browser.extra_args == ["--no-sandbox", "--disable-dev-shm-usage"]
