from apartment_hunter.criteria import (
    Criteria,
    HomeType,
    Pets,
    criteria_from_json,
    criteria_to_json,
    summarize,
)
from conftest import CHICAGO, LINCOLN_PARK


def test_summary_lists_criteria_in_a_fixed_order():
    c = Criteria(CHICAGO, max_price=2400, min_beds=2, pets=Pets.DOGS)
    assert summarize(c) == "Chicago, IL · 2+ bd · up to $2,400 · dogs"


def test_summary_formats_every_field():
    c = Criteria(CHICAGO, min_price=1500, max_price=2400, min_beds=0, min_baths=1.5,
                 home_type=HomeType.HOUSE, pets=Pets.CATS)
    assert summarize(c) == "Chicago, IL · studio+ · 1.5+ ba · $1,500–$2,400 · houses · cats"


def test_summary_min_price_only_other_home_types_and_area_only():
    assert summarize(Criteria(CHICAGO, min_price=1500)) == "Chicago, IL · from $1,500"
    assert summarize(Criteria(CHICAGO, home_type=HomeType.APARTMENT_CONDO)) == (
        "Chicago, IL · apartments & condos"
    )
    assert summarize(Criteria(CHICAGO, home_type=HomeType.TOWNHOME)) == "Chicago, IL · townhomes"
    assert summarize(Criteria(CHICAGO)) == "Chicago, IL"


def test_summary_shows_whole_numbers_without_a_trailing_point_zero():
    # Discord hands number options over as floats, so 2 beds can arrive as 2.0.
    c = Criteria(CHICAGO, min_beds=2.0, min_baths=2.0)
    assert summarize(c) == "Chicago, IL · 2+ bd · 2+ ba"


def test_criteria_round_trip_through_json():
    c = Criteria(LINCOLN_PARK, min_price=1500, max_price=2400, min_beds=2, min_baths=1,
                 home_type=HomeType.APARTMENT_CONDO, pets=Pets.DOGS)
    assert criteria_from_json(criteria_to_json(c)) == c
