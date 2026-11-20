"""Posting to Discord from the Scheduler's thread, through the bot's event loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import discord

from apartment_hunter.models import Listing
from apartment_hunter.notifiers import ChannelGone, NotifyError
from apartment_hunter.notifiers.discord import build_embed

log = logging.getLogger(__name__)


class DiscordBridge:
    """Lets plain threads post through a running discord.Client. Never call it from the event loop itself."""

    def __init__(self, client: discord.Client, timeout: float = 30.0) -> None:
        self._client = client
        self._timeout = timeout

    def post(self, channel_id: int, *, content: str | None = None, embed: dict | None = None) -> None:
        """Send a message to a channel. Raises ChannelGone or NotifyError when Discord didn't take it."""
        try:
            self._call(self._post(channel_id, content, embed))
        except (discord.NotFound, discord.Forbidden) as error:
            raise ChannelGone(f"Channel {channel_id} is gone or closed to the bot: {error}") from error
        except discord.HTTPException as error:
            raise NotifyError(f"Discord didn't take the message for channel {channel_id}: {error}") from error
        except TimeoutError as error:
            raise NotifyError(f"Discord didn't answer within {self._timeout:g} s") from error
        except RuntimeError as error:  # the event loop is closed or not running yet
            raise NotifyError(f"The bot isn't connected to Discord: {error}") from error

    def direct_message(self, user_id: int, text: str) -> None:
        """Send someone a direct message; failures are logged, not raised (they may block DMs)."""
        try:
            self._call(self._direct_message(user_id, text))
        except Exception:
            log.warning("Couldn't send a direct message to user %s", user_id, exc_info=True)

    def _call(self, coro: Coroutine[Any, Any, None]) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._client.loop)
        except BaseException:
            coro.close()  # never scheduled; don't leave it un-awaited
            raise
        try:
            future.result(self._timeout)
        except TimeoutError:
            future.cancel()
            raise

    async def _post(self, channel_id: int, content: str | None, embed: dict | None) -> None:
        channel = self._client.get_channel(channel_id) or await self._client.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise ChannelGone(f"Channel {channel_id} can't take messages")
        mentions = discord.AllowedMentions.none()
        if embed is None:
            await channel.send(content, allowed_mentions=mentions)
        else:
            await channel.send(content, embed=discord.Embed.from_dict(embed), allowed_mentions=mentions)

    async def _direct_message(self, user_id: int, text: str) -> None:
        user = self._client.get_user(user_id) or await self._client.fetch_user(user_id)
        await user.send(text, allowed_mentions=discord.AllowedMentions.none())


class ChannelNotifier:
    """The Notifier for one Search: posts its Alerts in its channel."""

    def __init__(self, bridge: DiscordBridge, channel_id: int) -> None:
        self._bridge = bridge
        self._channel_id = channel_id

    def send_listing(self, listing: Listing, search_name: str, previous_price: int | None = None) -> None:
        self._bridge.post(self._channel_id, embed=build_embed(listing, search_name, previous_price))

    def send_text(self, message: str) -> None:
        self._bridge.post(self._channel_id, content=message)
