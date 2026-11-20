"""The owner's settings for the bot, read from environment variables (see `.env.example`)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES = 10
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class SettingsError(Exception):
    """A setting is missing or unusable; the message names the variable."""


@dataclass(frozen=True)
class BotSettings:
    token: str
    data_dir: Path = Path("data")
    owner_id: int | None = None
    dev_guild_id: int | None = None
    interval_minutes: float = 15
    per_server: int = 5
    total: int = 25
    allowed_servers: frozenset[int] = frozenset()
    log_level: str = "INFO"


def load_settings(env: Mapping[str, str]) -> BotSettings:
    """Read the settings from `env`. Raises SettingsError naming the variable that is missing or bad."""
    token = env.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise SettingsError("DISCORD_TOKEN is not set. Put your bot's token in .env (see .env.example).")
    defaults = BotSettings(token=token)
    interval = _number(env, "INTERVAL_MINUTES", float, defaults.interval_minutes)
    if interval < MIN_INTERVAL_MINUTES:
        log.warning("INTERVAL_MINUTES=%g is below the minimum; using %d.", interval, MIN_INTERVAL_MINUTES)
        interval = MIN_INTERVAL_MINUTES
    return BotSettings(
        token=token,
        data_dir=Path(env.get("DATA_DIR", "").strip() or defaults.data_dir),
        owner_id=_number(env, "OWNER_USER_ID", int, None),
        dev_guild_id=_number(env, "DEV_GUILD_ID", int, None),
        interval_minutes=interval,
        per_server=_number(env, "MAX_SEARCHES_PER_SERVER", int, defaults.per_server),
        total=_number(env, "MAX_SEARCHES_TOTAL", int, defaults.total),
        allowed_servers=_ids(env, "ALLOWED_SERVERS"),
        log_level=_log_level(env, "LOG_LEVEL", defaults.log_level),
    )


def _number[N: (int, float), D](
    env: Mapping[str, str], name: str, parse: Callable[[str], N], default: D
) -> N | D:
    value = env.get(name, "").strip()
    if not value:
        return default
    try:
        return parse(value)
    except ValueError:
        raise SettingsError(f"{name} must be a number, not {value!r}.") from None


def _ids(env: Mapping[str, str], name: str) -> frozenset[int]:
    value = env.get(name, "")
    try:
        return frozenset(int(part) for part in value.split(",") if part.strip())
    except ValueError:
        raise SettingsError(
            f"{name} must be server ids separated by commas, not {value.strip()!r}."
        ) from None


def _log_level(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, "").strip().upper() or default
    if value not in LOG_LEVELS:
        raise SettingsError(f"{name} must be one of {', '.join(LOG_LEVELS)}, not {value!r}.")
    return value
