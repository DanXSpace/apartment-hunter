"""What a Search looks for: the Area it covers and the Criteria a Listing must meet."""

import json
from dataclasses import asdict, dataclass
from enum import StrEnum


class HomeType(StrEnum):
    ANY = "any"
    APARTMENT_CONDO = "apartment_condo"
    HOUSE = "house"
    TOWNHOME = "townhome"


class Pets(StrEnum):
    NONE = "none"
    DOGS = "dogs"
    CATS = "cats"


@dataclass(frozen=True)
class Area:
    """A place, resolved once when a Search is created or its area changes, then saved with the Search.

    `kind` is "city", "neighborhood" or "zipcode". The Zillow region and the Craigslist site plus
    radius are what each Source searches by.
    """

    display: str
    kind: str
    zillow_region_id: int
    zillow_region_type: int
    lat: float
    lon: float
    craigslist_site: str
    craigslist_radius_miles: int


@dataclass(frozen=True)
class Criteria:
    """What a Search looks for. Prices are monthly rent; a studio counts as 0 beds."""

    area: Area
    min_price: int | None = None
    max_price: int | None = None
    min_beds: float | None = None
    min_baths: float | None = None
    home_type: HomeType = HomeType.ANY
    pets: Pets = Pets.NONE


_HOME_TYPE_LABELS = {
    HomeType.APARTMENT_CONDO: "apartments & condos",
    HomeType.HOUSE: "houses",
    HomeType.TOWNHOME: "townhomes",
}


def _count(n: float) -> str:
    """2 and 2.0 both read "2"; 1.5 stays "1.5"."""
    return str(int(n)) if float(n).is_integer() else str(n)


def summarize(criteria: Criteria) -> str:
    parts = [criteria.area.display]
    if criteria.min_beds is not None:
        parts.append("studio+" if criteria.min_beds == 0 else f"{_count(criteria.min_beds)}+ bd")
    if criteria.min_baths is not None:
        parts.append(f"{_count(criteria.min_baths)}+ ba")
    if criteria.min_price is not None and criteria.max_price is not None:
        parts.append(f"${criteria.min_price:,}–${criteria.max_price:,}")
    elif criteria.max_price is not None:
        parts.append(f"up to ${criteria.max_price:,}")
    elif criteria.min_price is not None:
        parts.append(f"from ${criteria.min_price:,}")
    if criteria.home_type is not HomeType.ANY:
        parts.append(_HOME_TYPE_LABELS[criteria.home_type])
    if criteria.pets is not Pets.NONE:
        parts.append(criteria.pets.value)
    return " · ".join(parts)


def criteria_to_json(criteria: Criteria) -> str:
    data = asdict(criteria)
    data["home_type"] = criteria.home_type.value
    data["pets"] = criteria.pets.value
    return json.dumps(data)


def criteria_from_json(text: str) -> Criteria:
    data = json.loads(text)
    data["area"] = Area(**data["area"])
    data["home_type"] = HomeType(data["home_type"])
    data["pets"] = Pets(data["pets"])
    return Criteria(**data)
