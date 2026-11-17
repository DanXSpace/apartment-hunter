from apartment_hunter.notifiers.discord import build_embed
from conftest import make_listing


def test_embed_formats_ranges_and_studio():
    listing = make_listing(price_min=1515, price_max=2410, beds_min=0, beds_max=2,
                           baths_min=1, baths_max=1.5)
    fields = {f["name"]: f["value"] for f in build_embed(listing, "Downtown")["fields"]}
    assert fields["💰 Price"] == "**$1,515 – $2,410/mo**"
    assert fields["🛏 Beds"] == "Studio – 2"
    assert fields["🛁 Baths"] == "1 – 1.5"


def test_embed_price_drop():
    embed = build_embed(make_listing(price=1900), "Downtown", previous_price=2100)
    assert "$2,100 → $1,900" in embed["description"]


def test_embed_unknown_price():
    embed = build_embed(make_listing(price=None), "Downtown")
    assert embed["fields"][0]["value"] == "Call for price"
