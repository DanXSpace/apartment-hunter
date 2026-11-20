"""What a Search looks for: the Area it covers and the Criteria a Listing must meet."""

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from apartment_hunter.countries import format_price
from apartment_hunter.text import plain_number


class HomeType(StrEnum):
    ANY = "any"
    APARTMENT_CONDO = "apartment_condo"
    HOUSE = "house"
    TOWNHOME = "townhome"


class Pets(StrEnum):
    NONE = "none"
    CATS = "cats"
    SMALL_DOGS = "small_dogs"
    LARGE_DOGS = "large_dogs"


SourceLocation = dict[str, str | int | float]
"""What one Source needs to search an Area (a region id, a site and radius, a URL slug…), as JSON-safe
values. Each Source decides its own keys."""


@dataclass(frozen=True)
class Area:
    """A place, resolved once when a Search is created or its area changes, then saved with the Search.

    `kind` is "city", "neighborhood" or "zipcode"; `country` is a code from the countries table.
    `bbox` is (south, west, north, east) when the place lookup gives one. `locations` holds the source
    location of each Source that placed this Area, keyed by Source key.
    """

    display: str
    kind: str
    country: str
    lat: float
    lon: float
    bbox: tuple[float, float, float, float] | None = None
    locations: dict[str, SourceLocation] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """The place's own name, the first part of `display`: "Camden Town", "SW11 6HR"."""
        return self.display.split(", ")[0]

    @property
    def context(self) -> tuple[str, ...]:
        """The parts of `display` between the name and the country: ("London",) for "Camden Town,
        London, United Kingdom", ("Chicago", "IL") for "Lincoln Park, Chicago, IL, United States"."""
        return tuple(self.display.split(", ")[1:-1])


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


def summarize(criteria: Criteria) -> str:
    def price(amount: int) -> str:
        return format_price(amount, criteria.area.country)

    parts = [criteria.area.display]
    if criteria.min_beds is not None:
        parts.append("studio+" if criteria.min_beds == 0 else f"{plain_number(criteria.min_beds)}+ bd")
    if criteria.min_baths is not None:
        parts.append(f"{plain_number(criteria.min_baths)}+ ba")
    if criteria.min_price is not None and criteria.max_price is not None:
        parts.append(f"{price(criteria.min_price)}–{price(criteria.max_price)}")
    elif criteria.max_price is not None:
        parts.append(f"up to {price(criteria.max_price)}")
    elif criteria.min_price is not None:
        parts.append(f"from {price(criteria.min_price)}")
    if criteria.home_type is not HomeType.ANY:
        parts.append(_HOME_TYPE_LABELS[criteria.home_type])
    if criteria.pets is not Pets.NONE:
        parts.append(criteria.pets.value.replace("_", " "))
    return " · ".join(parts)


def criteria_to_json(criteria: Criteria) -> str:
    data = asdict(criteria)
    data["home_type"] = criteria.home_type.value
    data["pets"] = criteria.pets.value
    return json.dumps(data)


def _area_from_json(data: dict[str, Any]) -> Area:
    if "country" not in data:  # saved before countries existed: a US Area with Zillow and Craigslist fields
        data = {
            "display": data["display"], "kind": data["kind"], "country": "US",
            "lat": data["lat"], "lon": data["lon"],
            "locations": {
                "zillow": {"region_id": data["zillow_region_id"], "region_type": data["zillow_region_type"]},
                "craigslist": {"site": data["craigslist_site"],
                               "radius_miles": data["craigslist_radius_miles"]},
            },
        }
    bbox = data.get("bbox")
    return Area(**{**data, "bbox": tuple(bbox) if bbox is not None else None})


def criteria_from_json(text: str) -> Criteria:
    data = json.loads(text)
    data["area"] = _area_from_json(data["area"])
    data["home_type"] = HomeType(data["home_type"])
    data["pets"] = Pets(data["pets"])
    return Criteria(**data)
