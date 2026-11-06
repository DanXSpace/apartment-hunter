import sys

import pytest

from apartment_hunter.appdata import DataDir
from apartment_hunter.cli import main

URL = "https://www.apartments.com/philadelphia-pa/"
CONFIG = f'webhook_url = "https://hook"\n[[search]]\nname = "a"\nurl = "{URL}"\n'


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for var in ("APARTMENT_HUNTER_CONFIG", "DATABASE_PATH", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)


def test_second_launch_against_same_data_exits_with_clear_message(tmp_path, caplog):
    (tmp_path / "config.toml").write_text(CONFIG)
    with DataDir(tmp_path / "data").lock():  # a first launch, still running
        assert main([]) == 2
    assert "already running" in caplog.text
    assert str((tmp_path / "data").resolve()) in caplog.text


@pytest.mark.skipif(sys.platform != "linux", reason="XDG_DATA_HOME is the Linux convention")
def test_app_data_flag_points_the_cli_at_the_app_data_dir(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    app_data = tmp_path / "xdg" / "apartment-hunter"
    app_data.mkdir(parents=True)
    (app_data / "config.toml").write_text(CONFIG)
    with DataDir(app_data).lock():  # the desktop app, running
        assert main(["--app-data"]) == 2
    assert "already running" in caplog.text
    assert str(app_data.resolve()) in caplog.text
