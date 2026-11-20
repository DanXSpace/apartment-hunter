"""What the Sources share, so each Source module keeps only its site-specific parsing: the plain-language
messages built from a Source's display name, the place-lookup request, telling a block page from a
changed layout, URL slugs, rents per month, and matching place names when placing an Area."""

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence

from apartment_hunter.criteria import Area, SourceLocation
from apartment_hunter.sources import PLACE_NOTE, BlockedError, CantPlace, ScrapeError
from apartment_hunter.sources.http import HttpClient, HttpResponse, raise_for_status

# Months per rent period: a weekly rent is 52 weeks over 12 months, so £500 pw is £2,167 pcm.
MONTHS_PER = {"day": 12 / 365, "week": 12 / 52, "month": 1, "quarter": 3, "year": 12}


def blocked(name: str) -> str:
    return (f"{name} is blocking requests from this computer right now (bot protection). "
            "This usually clears up on its own.")


def layout_changed(name: str, what: str = "page layout") -> str:
    return f"{name}'s {what} changed, so listings couldn't be read. The bot needs an update."


def http_error(name: str, status: int) -> str:
    return f"{name} answered with an error (HTTP {status})."


def lookup_down(name: str) -> str:
    return f"{name}'s place lookup isn't answering right now. Try again in a few minutes."


def doesnt_know(name: str, area: Area) -> CantPlace:
    """The reason a Source is left out when its site has no such place."""
    return CantPlace(f"{name} doesn't know {area.display}.")


def no_postcodes(name: str, instead: str) -> CantPlace:
    """The reason a Source that can't search by postcode is left out ("the neighbourhood or city")."""
    return CantPlace(f"{name} can't search by postcode. Try {instead} instead.")


def check_response(response: HttpResponse, name: str) -> None:
    """A results page's status: 403 and 429 are BlockedError, any other error a plain ScrapeError."""
    raise_for_status(response, blocked=blocked(name), failed=http_error(name, response.status))


def unreadable(html: str, name: str, markers: Sequence[str], layout: str | None = None) -> ScrapeError:
    """The error for a page whose listing data is missing: BlockedError when it is a block page (it
    holds one of `markers`), else the layout changed. Look for markers only once the data is missing,
    since a listing's own text could hold the words."""
    if any(marker in html for marker in markers):
        return BlockedError(blocked(name))
    return ScrapeError(layout or layout_changed(name))


def look_up(
    http: HttpClient,
    url: str,
    name: str,
    *,
    down: str | None = None,
    left_out: Callable[[int], str | None] = lambda status: None,
) -> str:
    """One request to a Source's place lookup, answering its body. When the lookup can't be reached or
    answers with an error, raises ScrapeError(`down`, default lookup_down(name)) so the create or edit
    is refused; when `left_out(status)` gives a reason, raises CantPlace(reason) instead, so only this
    Source is left out."""
    down = down or lookup_down(name)
    try:
        response = http.get(url)
    except Exception as error:
        raise ScrapeError(down) from error
    reason = left_out(response.status)
    if reason is not None:
        raise CantPlace(reason)
    raise_for_status(response, blocked=down, failed=down)
    return response.text


def slug(text: str, spellings: Mapping[str, str] | None = None) -> str:
    """A name as sites write it in URLs: lower case, accents dropped, anything else a hyphen
    ("Saint-Étienne" is "saint-etienne"). `spellings` replace letters first (German "ö" as "oe")."""
    lowered = text.casefold()
    for letter, spelled in (spellings or {}).items():
        lowered = lowered.replace(letter, spelled)
    plain = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def per_month(amount: float, period: str) -> int | None:
    """A rent per calendar month from a rent per `period` (a key of MONTHS_PER); None for another period."""
    months = MONTHS_PER.get(period)
    return round(amount / months) if months is not None else None


def same_name(a: str, b: str) -> bool:
    """Two place names are the same, ignoring case, accents, spaces and punctuation: "St. John's Wood"
    and "St Johns Wood", "Chamberí" and "Chamberi"."""
    return slug(a).replace("-", "") == slug(b).replace("-", "")


def find_by_name[T](area: Area, name: str, find: Callable[[str], T | None]) -> tuple[T, str | None]:
    """What `find` (a Source's place lookup for a text) finds for the Area's own name, with no note.
    A full postcode the site doesn't know falls back to its outward code ("SW11" for "SW11 6HR"), with a
    note saying so; anything else it doesn't know raises doesnt_know."""
    found = find(area.name)
    if found is not None:
        return found, None
    outward = area.name.split(" ")[0]
    found = find(outward) if area.kind == "zipcode" and outward != area.name else None
    if found is None:
        raise doesnt_know(name, area)
    return found, f"{name} doesn't know {area.name}, so it searches all of {outward}."


def with_note(location: SourceLocation, note: str | None) -> SourceLocation:
    """A source location carrying `note` (a broader area searched than the one picked), if any."""
    return {**location, PLACE_NOTE: note} if note else location


def in_context(text: str, area: Area) -> bool:
    """`text` (a site's description of a place) mentions the rest of the Area: "London" for "Camden
    Town, London, United Kingdom"."""
    return any(part.casefold() in text.casefold() for part in area.context)
