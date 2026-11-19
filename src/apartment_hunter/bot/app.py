"""The discord.py client and the /search commands. Thin: the SearchService decides, replies.py words it."""

# No `from __future__ import annotations`: discord.py reads the command parameters' annotations at runtime.

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import discord
from discord import app_commands

from apartment_hunter.bot.replies import (
    checking_reply,
    created_reply,
    help_reply,
    invite_url,
    refusal,
    statuses_reply,
)
from apartment_hunter.criteria import HomeType, Pets
from apartment_hunter.models import Search
from apartment_hunter.service import NewSearch, Pending, SearchError, SearchService
from apartment_hunter.settings import BotSettings

log = logging.getLogger(__name__)

FIRST_OUTCOME_WAIT_SECONDS = 840  # an interaction's reply can be edited for 15 minutes
LATE_OUTCOME_POLL_SECONDS = 60
SOMETHING_WENT_WRONG = "Something went wrong. The owner can find details in the log."

HOME_TYPES = [
    app_commands.Choice(name="Any", value=HomeType.ANY.value),
    app_commands.Choice(name="Apartment or condo", value=HomeType.APARTMENT_CONDO.value),
    app_commands.Choice(name="House", value=HomeType.HOUSE.value),
    app_commands.Choice(name="Townhome", value=HomeType.TOWNHOME.value),
]
PETS = [
    app_commands.Choice(name="No requirement", value=Pets.NONE.value),
    app_commands.Choice(name="Dogs", value=Pets.DOGS.value),
    app_commands.Choice(name="Cats", value=Pets.CATS.value),
]
SOURCES = [
    app_commands.Choice(name="Zillow and Craigslist", value="both"),
    app_commands.Choice(name="Zillow only", value="zillow"),
    app_commands.Choice(name="Craigslist only", value="craigslist"),
]


class _Bot(discord.Client):
    def __init__(self, settings: BotSettings, on_first_ready: Callable[[], None]) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        # The bot never joins voice, so discord.py's "voice will NOT be supported" warnings are noise.
        discord.VoiceClient.warn_nacl = discord.VoiceClient.warn_dave = False
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._settings = settings
        self._on_first_ready: Callable[[], None] | None = on_first_ready
        self.background: set[asyncio.Task[None]] = set()

    async def setup_hook(self) -> None:
        if self._settings.dev_guild_id is not None:
            guild = discord.Object(id=self._settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Registered %d commands in the development server %s", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            log.info("Registered %d commands globally (they can take up to an hour to appear)", len(synced))

    async def on_ready(self) -> None:
        log.info("Connected to Discord as %s. Invite link: %s",
                 self.user, invite_url(self.application_id or 0))
        if self._on_first_ready is not None:
            on_first_ready, self._on_first_ready = self._on_first_ready, None
            on_first_ready()


def _can_post(interaction: discord.Interaction) -> bool:
    channel, guild = interaction.channel, interaction.guild
    if guild is None or channel is None or isinstance(channel, discord.DMChannel | discord.GroupChannel):
        return False
    permissions = channel.permissions_for(guild.me)
    return permissions.view_channel and permissions.send_messages and permissions.embed_links


def build_client(
    service: SearchService, settings: BotSettings, on_first_ready: Callable[[], None]
) -> discord.Client:
    bot = _Bot(settings, on_first_ready)
    search = app_commands.Group(
        name="search", description="Find new rentals and get them posted here", guild_only=True
    )

    @search.command(name="new", description="Start watching an area for new rentals")
    @app_commands.describe(
        area="A city, neighborhood or ZIP code",
        max_price="Highest monthly rent in dollars",
        min_price="Lowest monthly rent in dollars",
        min_beds="Fewest bedrooms (0 = studio)",
        min_baths="Fewest bathrooms",
        home_type="Kind of place",
        pets="Pets that must be allowed",
        sources="Where to look (both if you leave it out)",
        name="A name for this search (made from the criteria if you leave it out)",
    )
    @app_commands.choices(home_type=HOME_TYPES, pets=PETS, sources=SOURCES)
    async def new(
        interaction: discord.Interaction,
        area: str,
        max_price: app_commands.Range[int, 1, 100000] | None = None,
        min_price: app_commands.Range[int, 1, 100000] | None = None,
        min_beds: app_commands.Range[int, 0, 10] | None = None,
        min_baths: app_commands.Range[float, 0, 10] | None = None,
        home_type: app_commands.Choice[str] | None = None,
        pets: app_commands.Choice[str] | None = None,
        sources: app_commands.Choice[str] | None = None,
        name: app_commands.Range[str, 1, 60] | None = None,
    ) -> None:
        assert interaction.guild is not None and interaction.channel is not None  # guild_only
        can_post = _can_post(interaction)
        request = NewSearch(
            area=area,
            min_price=min_price,
            max_price=max_price,
            min_beds=min_beds,
            min_baths=min_baths,
            home_type=HomeType(home_type.value) if home_type else HomeType.ANY,
            pets=Pets(pets.value) if pets else Pets.NONE,
            name=name,
            sources=(sources.value,) if sources and sources.value != "both" else None,
        )
        await interaction.response.defer(thinking=True)
        try:
            created, pending = await asyncio.to_thread(
                service.create, interaction.guild.id, interaction.channel.id, interaction.user.id, request,
                can_post=can_post,
            )
        except SearchError as error:
            await interaction.delete_original_response()
            await interaction.followup.send(refusal(error), ephemeral=True)
            return
        await interaction.edit_original_response(content=checking_reply(created))
        outcome = await asyncio.to_thread(pending.wait, FIRST_OUTCOME_WAIT_SECONDS)
        await interaction.edit_original_response(
            content=created_reply(created, outcome, settings.interval_minutes)
        )
        if outcome is None:
            task = asyncio.create_task(_post_when_done(bot, created, pending, settings.interval_minutes))
            bot.background.add(task)
            task.add_done_callback(bot.background.discard)

    @new.autocomplete("area")
    async def area_suggestions(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        names = await asyncio.to_thread(service.suggest_areas, current)
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]

    @search.command(name="list", description="Show this server's searches and how they're doing")
    async def list_(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None  # guild_only
        statuses = await asyncio.to_thread(service.statuses, interaction.guild.id)
        await interaction.response.send_message(statuses_reply(statuses, datetime.now(UTC)), ephemeral=True)

    @search.command(name="help", description="What this bot does and how to use it")
    async def help_(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            help_reply(service.limits, settings.interval_minutes), ephemeral=True
        )

    @bot.tree.error
    async def on_command_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("A command failed", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(SOMETHING_WENT_WRONG, ephemeral=True)
            else:
                await interaction.response.send_message(SOMETHING_WENT_WRONG, ephemeral=True)
        except discord.HTTPException:
            log.warning("Couldn't tell the user the command failed", exc_info=True)

    bot.tree.add_command(search)
    return bot


async def _post_when_done(bot: discord.Client, search: Search, pending: Pending, interval: float) -> None:
    """Post the reply to /search new in its channel once a slow first check finishes."""
    outcome = None
    while outcome is None and not bot.is_closed():
        # Bounded waits, so a Search deleted before its first Run doesn't hold a thread forever.
        outcome = await asyncio.to_thread(pending.wait, LATE_OUTCOME_POLL_SECONDS)
    if outcome is None:
        return
    channel = bot.get_channel(search.channel_id)
    if isinstance(channel, discord.abc.Messageable):
        try:
            await channel.send(created_reply(search, outcome, interval),
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("[%s] Couldn't post the first check's result", search.name, exc_info=True)
