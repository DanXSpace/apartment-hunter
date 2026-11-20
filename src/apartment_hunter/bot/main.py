"""`apartment-hunter`: start the Discord bot with settings from the environment (and `.env`)."""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from apartment_hunter.appdata import LOG_FORMAT, AlreadyRunning, DataDir
from apartment_hunter.areas import PHOTON_USER_AGENT, AreaLookup
from apartment_hunter.bot.app import build_client
from apartment_hunter.bot.posting import ChannelNotifier, DiscordBridge
from apartment_hunter.countries import country
from apartment_hunter.runner import SearchRunner
from apartment_hunter.scheduler import Scheduler
from apartment_hunter.service import Limits, SearchService
from apartment_hunter.settings import BotSettings, SettingsError, load_settings
from apartment_hunter.sources import Source
from apartment_hunter.sources.bienici import BieniciSource
from apartment_hunter.sources.craigslist import CraigslistSource
from apartment_hunter.sources.http import CurlHttpClient, PoliteHttpClient
from apartment_hunter.sources.idealista import ITALY, PORTUGAL, IdealistaSource
from apartment_hunter.sources.rightmove import RightmoveSource
from apartment_hunter.sources.zillow import ZillowSource
from apartment_hunter.storage import Store

log = logging.getLogger("apartment_hunter")

DESCRIPTION = """\
Run the Apartment Hunter Discord bot. People add it to their server and use /search to get new
rentals posted in a channel: Zillow and Craigslist in the US, Rightmove in the UK (the sources
option picks one site or all of the area's country).

Settings come from environment variables, or from a .env file in the project folder
(see .env.example). DISCORD_TOKEN is required.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apartment-hunter", description=DESCRIPTION,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    load_dotenv()
    try:
        settings = load_settings(os.environ)
    except SettingsError as error:
        log.error("%s", error)
        return 2
    logging.getLogger().setLevel(settings.log_level)

    data = DataDir(settings.data_dir)
    try:
        with data.lock():
            logging.getLogger().addHandler(data.log_handler())
            return _run(settings, data)
    except AlreadyRunning as error:
        log.error("%s", error)
        return 2


def _run(settings: BotSettings, data: DataDir) -> int:
    store = Store(data.database)
    run_http = PoliteHttpClient(CurlHttpClient())
    # Autocomplete must answer within Discord's 3 seconds, so lookups never wait behind polite gaps.
    areas = AreaLookup(CurlHttpClient(timeout=2.5, headers={"User-Agent": PHOTON_USER_AGENT}))
    # Placing an Area happens once per create or Area edit, outside Runs, so it skips the polite gaps.
    place_http = CurlHttpClient()
    sources: dict[str, Source] = {
        "zillow": ZillowSource(run_http, lookup_http=place_http),
        "craigslist": CraigslistSource(run_http),
        "rightmove": RightmoveSource(run_http, lookup_http=place_http),
        "idealista_it": IdealistaSource(ITALY, run_http),
        "idealista_pt": IdealistaSource(PORTUGAL, run_http),
        "bienici": BieniciSource(run_http, lookup_http=place_http),
    }

    # The lambdas look `bridge` and `service` up when called, after all of them exist.
    runner = SearchRunner(sources, store, lambda search: ChannelNotifier(
        bridge, search.channel_id, country(search.criteria.area.country).currency))
    scheduler = Scheduler(lambda search_id: service.run(search_id), settings.interval_minutes)
    service = SearchService(
        store=store, runner=runner, scheduler=scheduler, areas=areas, sources=sources,
        limits=Limits(per_server=settings.per_server, total=settings.total),
        interval_minutes=settings.interval_minutes,
        direct_message=lambda user_id, text: bridge.direct_message(user_id, text),
        owner_id=settings.owner_id,
    )

    def on_first_ready() -> None:
        service.load()
        scheduler.start_in_background()

    client = build_client(service, settings, on_first_ready)
    bridge = DiscordBridge(client)
    try:
        client.run(settings.token, log_handler=None)
    finally:
        scheduler.shutdown(timeout=30)
        store.close()
    return 0
