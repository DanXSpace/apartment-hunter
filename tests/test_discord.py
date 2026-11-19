from apartment_hunter.notifiers.discord import build_embed
from conftest import make_listing


def test_embed_formats_ranges_and_studio():
    listing = make_listing(price_min=1515, price_max=2410, beds_min=0, beds_max=2,
                           baths_min=1, baths_max=1.5)
    fields = {f["name"]: f["value"] for f in build_embed(listing, "Chicago")["fields"]}
    assert fields["💰 Price"] == "**$1,515 – $2,410/mo**"
    assert fields["🛏 Beds"] == "Studio – 2"
    assert fields["🛁 Baths"] == "1 – 1.5"


def test_embed_names_the_source_the_search_and_the_time():
    e = build_embed(make_listing(name=""), "Chicago")
    assert e["description"] == "New listing on Zillow"
    assert e["footer"] == {"text": "Chicago"}
    assert e["timestamp"] == "2026-11-05T12:00:00+00:00"


def test_embed_price_drop():
    e = build_embed(make_listing(price=1900, source="craigslist"), "Chicago", previous_price=2100)
    assert e["description"].endswith("📉 Price drop on Craigslist: $2,100 → $1,900")


def test_embed_unknown_price():
    embed = build_embed(make_listing(price=None), "Chicago")
    assert embed["fields"][0]["value"] == "Call for price"
