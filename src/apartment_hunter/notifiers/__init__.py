from typing import Protocol

from apartment_hunter.models import Listing


class NotifyError(Exception):
    """The notification was not delivered; the listing must not be marked as seen."""


class ChannelGone(NotifyError):
    """The channel is gone, or the bot may no longer post there; stop sending to it."""


class Notifier(Protocol):
    def send_listing(self, listing: Listing, search_name: str,
                     previous_price: int | None = None) -> None: ...

    def send_text(self, message: str) -> None: ...
