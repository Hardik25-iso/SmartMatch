"""Pune area gazetteer and distance helpers."""
import math

# Approximate centroids (lat, lng) of Pune localities. Includes every area used by
# the seed-db so existing Orphanage.city values resolve.
PUNE_AREAS = {
    "Kothrud": (18.5074, 73.8077),
    "Shivaji Nagar": (18.5308, 73.8475),
    "Deccan Gymkhana": (18.5158, 73.8411),
    "Koregaon Park": (18.5362, 73.8940),
    "Hadapsar": (18.5089, 73.9260),
    "Baner": (18.5590, 73.7868),
    "Aundh": (18.5580, 73.8075),
    "Viman Nagar": (18.5679, 73.9143),
    "Katraj": (18.4575, 73.8677),
    "Camp": (18.5122, 73.8800),
    "Wakad": (18.5987, 73.7688),
    "Hinjewadi": (18.5912, 73.7389),
    "Pimpri": (18.6298, 73.7997),
    "Chinchwad": (18.6446, 73.7922),
    "Kharadi": (18.5515, 73.9348),
    "Wagholi": (18.5808, 73.9787),
    "Kondhwa": (18.4695, 73.8890),
    "Bibwewadi": (18.4708, 73.8636),
    "Swargate": (18.5018, 73.8636),
    "Sinhagad Road": (18.4760, 73.8200),
    "Warje": (18.4838, 73.7968),
    "Pashan": (18.5387, 73.7924),
    "Yerawada": (18.5529, 73.8797),
    "Magarpatta": (18.5158, 73.9272),
    "Undri": (18.4550, 73.9180),
    "NIBM": (18.4750, 73.9060),
    "Balewadi": (18.5767, 73.7797),
    "Sangvi": (18.5667, 73.8167),
    "Dhanori": (18.5916, 73.8997),
    "Lohegaon": (18.5930, 73.9270),
}

AREA_NAMES = sorted(PUNE_AREAS)
_LOOKUP = {name.lower().replace(" ", ""): name for name in PUNE_AREAS}


def normalize_area(value):
    """Map free text like 'shivaji nagar' or 'ShivajiNagar' to a canonical area, or None."""
    if not value:
        return None
    return _LOOKUP.get(str(value).strip().lower().replace(" ", ""))


def area_coords(value):
    name = normalize_area(value)
    return PUNE_AREAS.get(name) if name else None


def haversine_km(a, b):
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def area_distance_km(area_a, area_b):
    """Distance between two area names, or None if either is unknown."""
    ca, cb = area_coords(area_a), area_coords(area_b)
    if ca is None or cb is None:
        return None
    return haversine_km(ca, cb)
