import logging
import re

import pytest

from apartment_hunter.appdata import AlreadyRunning, DataDir


def test_second_acquire_in_same_data_dir_fails(tmp_path):
    data = DataDir(tmp_path)
    with data.lock():
        with pytest.raises(AlreadyRunning, match=re.escape(str(tmp_path.resolve()))):
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
