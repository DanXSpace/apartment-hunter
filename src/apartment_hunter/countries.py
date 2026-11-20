"""The supported countries: each one's name, currency and the Sources a Search there uses by default.

Adding a country means adding a row here and its Sources (see `apartment_hunter.sources`).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Country:
    """A supported country. `code` is ISO 3166-1 alpha-2 ("US"); `sources` are Source keys, in the
    order replies name them."""

    code: str
    name: str
    currency: str
    sources: tuple[str, ...]


COUNTRIES: dict[str, Country] = {
    "US": Country("US", "United States", "$", ("zillow", "craigslist")),
    "GB": Country("GB", "United Kingdom", "£", ("rightmove", "zoopla")),
    "FR": Country("FR", "France", "€", ("bienici",)),
    "ES": Country("ES", "Spain", "€", ("fotocasa",)),
    "IT": Country("IT", "Italy", "€", ("idealista_it",)),
    "PT": Country("PT", "Portugal", "€", ("idealista_pt",)),
    "NL": Country("NL", "Netherlands", "€", ("pararius",)),
}


def country(code: str) -> Country:
    """The supported country with this code; KeyError for any other."""
    return COUNTRIES[code]


def format_price(amount: float, country_code: str) -> str:
    """A whole amount in the country's currency: "$2,400", "£1,500", "€950"."""
    return f"{country(country_code).currency}{int(amount):,}"
