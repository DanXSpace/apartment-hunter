"""Distances on the Earth's surface, for Sources that place an Area by its coordinates."""

import math

EARTH_RADIUS_MILES = 3958.8


def distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """The great-circle distance between two points, in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))
