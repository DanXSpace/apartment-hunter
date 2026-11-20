from apartment_hunter.bot.main import DESCRIPTION


def test_description_names_every_country_and_source():
    text = " ".join(DESCRIPTION.split())
    for phrase in [
        "United States: Zillow and Craigslist",
        "United Kingdom: Rightmove and Zoopla",
        "Germany: Kleinanzeigen",
        "France: Bien'ici",
        "Spain: Fotocasa",
        "Italy: Idealista Italy",
        "Portugal: Idealista Portugal",
        "Netherlands: Pararius",
    ]:
        assert phrase in text
