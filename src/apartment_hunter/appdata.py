"""Where the app keeps its files, and the single-instance lock over them."""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

import platformdirs

APP_NAME = "apartment-hunter"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOCK_FILE = "apartment-hunter.lock"


class AlreadyRunning(Exception):
    pass


@dataclass(frozen=True)
class DataDir:
    """A directory of app state: config, database, browser profile, logs, lock and port file.

    The desktop app uses the per-user app-data location (`DataDir.app_data()`). The CLI uses
    the directory holding its database (`./data` by default), unless given `--app-data`.
    """

    root: Path

    @classmethod
    def app_data(cls) -> DataDir:
        """The standard per-user app-data directory for this OS."""
        return cls(Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)))

    @property
    def config(self) -> Path:
        return self.root / "config.toml"

    @property
    def database(self) -> Path:
        return self.root / "apartment_hunter.db"

    @property
    def browser_profile(self) -> Path:
        return self.root / "browser-profile"

    @property
    def log_file(self) -> Path:
        return self.root / "logs" / "apartment-hunter.log"

    @property
    def port_file(self) -> Path:
        return self.root / "port"

    @property
    def lock_file(self) -> Path:
        return self.root / LOCK_FILE

    def log_handler(self) -> logging.Handler:
        """A rotating handler for `log_file` (about 1 MB per file, 3 old files kept).

        Attach it only while holding `lock()`: two processes rotating one file corrupt it.
        """
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(self.log_file, maxBytes=1_000_000, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        return handler

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Hold the single-instance lock for this data directory.

        The lock is an OS file lock, so the OS drops it when its process exits, crashed or
        not. A lock file left behind by a dead process is therefore simply taken over.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        f = open(self.lock_file, "a+")  # noqa: SIM115 - held open for the life of the lock
        try:
            if not _try_lock(f):
                raise AlreadyRunning(
                    f"Apartment Hunter is already running with the data in {self.root.resolve()}"
                    f"{_pid_note(f)}. Stop it first, or use a different data directory."
                )
            f.seek(0)
            f.truncate()
            f.write(str(os.getpid()))
            f.flush()
            try:
                yield
            finally:
                _unlock(f)
        finally:
            f.close()


def _pid_note(f: IO[str]) -> str:
    try:
        f.seek(0)
        pid = f.read().strip()
    except OSError:  # Windows: the holder's locked region can't be read
        return ""
    return f" (process {pid})" if pid.isdigit() else ""


if sys.platform == "win32":
    import msvcrt

    def _try_lock(f: IO[str]) -> bool:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(f: IO[str]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(f: IO[str]) -> bool:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(f: IO[str]) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)
