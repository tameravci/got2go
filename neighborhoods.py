"""Seattle neighborhoods for the SEO landing pages.

Each shop goes to the neighborhood with the closest center point. A shop
farther than MAX_KM from every center gets no neighborhood. The centers
are approximate; they only need to sort shops into sensible groups.
"""
import math

MAX_KM = 2.5

# (slug, display name, lat, lng)
NEIGHBORHOODS = [
    ("downtown", "Downtown", 47.6062, -122.3321),
    ("belltown", "Belltown", 47.6150, -122.3460),
    ("pioneer-square", "Pioneer Square", 47.6015, -122.3343),
    ("chinatown-international-district", "Chinatown-International District", 47.5980, -122.3240),
    ("first-hill", "First Hill", 47.6075, -122.3255),
    ("capitol-hill", "Capitol Hill", 47.6200, -122.3190),
    ("central-district", "Central District", 47.6080, -122.3010),
    ("south-lake-union", "South Lake Union", 47.6270, -122.3366),
    ("eastlake", "Eastlake", 47.6420, -122.3260),
    ("queen-anne", "Queen Anne", 47.6374, -122.3570),
    ("magnolia", "Magnolia", 47.6500, -122.4000),
    ("interbay", "Interbay", 47.6430, -122.3770),
    ("fremont", "Fremont", 47.6505, -122.3500),
    ("wallingford", "Wallingford", 47.6615, -122.3348),
    ("university-district", "University District", 47.6614, -122.3132),
    ("ballard", "Ballard", 47.6687, -122.3847),
    ("phinney-ridge", "Phinney Ridge", 47.6750, -122.3540),
    ("green-lake", "Green Lake", 47.6800, -122.3260),
    ("roosevelt", "Roosevelt", 47.6780, -122.3170),
    ("ravenna", "Ravenna", 47.6750, -122.3020),
    ("greenwood", "Greenwood", 47.6900, -122.3550),
    ("northgate", "Northgate", 47.7060, -122.3260),
    ("lake-city", "Lake City", 47.7190, -122.2950),
    ("montlake", "Montlake", 47.6400, -122.3040),
    ("madison-park", "Madison Park", 47.6340, -122.2780),
    ("sodo", "SoDo", 47.5800, -122.3350),
    ("beacon-hill", "Beacon Hill", 47.5680, -122.3110),
    ("mount-baker", "Mount Baker", 47.5770, -122.2930),
    ("columbia-city", "Columbia City", 47.5600, -122.2870),
    ("georgetown", "Georgetown", 47.5470, -122.3210),
    ("west-seattle", "West Seattle", 47.5700, -122.3870),
]

BY_SLUG = {slug: {"slug": slug, "name": name, "lat": lat, "lng": lng}
           for slug, name, lat, lng in NEIGHBORHOODS}


def distance_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest(lat, lng):
    """Return the neighborhood dict for a point, or None if it is too far out."""
    if lat is None or lng is None:
        return None
    best, best_km = None, MAX_KM
    for slug, _name, nlat, nlng in NEIGHBORHOODS:
        km = distance_km(lat, lng, nlat, nlng)
        if km < best_km:
            best, best_km = BY_SLUG[slug], km
    return best
