import logging
import sys

import pytest

from apartment_hunter.appdata import AlreadyRunning, DataDir


def test_second_acquire_in_same_data_dir_fails(tmp_path):
    data = DataDir(tmp_path)
    with data.lock():
        with pytest.raises(AlreadyRunning, match=str(tmp_path.resolve())):
            with data.lock():
                pass


def test_lock_is_released_on_exit(tmp_path):
    data = DataDir(tmp_path)
    with data.lock():
        pass
    with data.lock():  # would raise AlreadyRunning if the first lock were still held
        pass


def test_stale_lock_file_from_crashed_process_is_taken_over(tmp_path):
    (tmp_path / "apartment-hunter.lock").write_text("999999")  # left behind, nobody holds it
    with DataDir(tmp_path).lock():
        pass


@pytest.mark.skipif(sys.platform != "linux", reason="XDG_DATA_HOME is the Linux convention")
def test_app_data_dir_is_the_per_user_data_location(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    data = DataDir.app_data()
    assert data.root == tmp_path / "apartment-hunter"
    assert data.config == tmp_path / "apartment-hunter" / "config.toml"
    assert data.database == tmp_path / "apartment-hunter" / "apartment_hunter.db"
    assert data.browser_profile == tmp_path / "apartment-hunter" / "browser-profile"
    assert data.log_file == tmp_path / "apartment-hunter" / "logs" / "apartment-hunter.log"
    assert data.port_file == tmp_path / "apartment-hunter" / "port"


def test_log_handler_writes_to_the_log_file_in_the_data_dir(tmp_path):
    data = DataDir(tmp_path)
    logger = logging.getLogger("test_appdata.log_handler")
    handler = data.log_handler()
    logger.addHandler(handler)
    try:
        logger.warning("Search Downtown failed")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert "Search Downtown failed" in (tmp_path / "logs" / "apartment-hunter.log").read_text()
