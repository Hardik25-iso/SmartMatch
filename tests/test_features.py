from datetime import datetime, timedelta

import pytest

from smartmatch.catalog import normalize_category, same_category
from smartmatch.features import (
    ContentModel, category_match_score, compute_candidates, is_open, need_freshness, proximity_score,
)
from smartmatch.geo import area_distance_km, haversine_km, normalize_area

NOW = datetime(2026, 9, 1, 12, 0)


def need(id, ngo, category="Food", item="Rice Bags (10kg)", age=0, status="Active", qty=50, urgent=False,
         pledged=None, donor=None):
    stamp = NOW - timedelta(days=age)
    return {"id": id, "orphanage_id": ngo, "item_name": item, "category": category, "quantity": qty,
            "is_urgent": urgent, "status": status, "date_posted": stamp, "last_updated": stamp,
            "pledge_timestamp": pledged, "is_stale": False, "donor_id": donor}


def test_haversine_known_pune_distances():
    # Kothrud to Viman Nagar is roughly 12 km in a straight line.
    assert 11 < area_distance_km("Kothrud", "Viman Nagar") < 13.5
    assert area_distance_km("Baner", "Baner") == pytest.approx(0.0)
    assert haversine_km((0, 0), (0, 1)) == pytest.approx(111.19, rel=1e-3)


def test_proximity_decreases_with_distance_and_is_neutral_when_unknown():
    assert proximity_score(0) == 1.0
    assert proximity_score(2) > proximity_score(5) > proximity_score(20)
    assert proximity_score(None) == 0.5


def test_area_and_category_normalization():
    assert normalize_area("shivaji nagar") == "Shivaji Nagar"
    assert normalize_area("VimanNagar") == "Viman Nagar"
    assert normalize_area("Mumbai") is None
    assert normalize_category("school supplies") == "School Supplies"
    assert same_category("FOOD", " food ")


def test_freshness_drops_past_30_days():
    assert need_freshness(need(1, 1, age=3), NOW) == 1.0
    assert need_freshness(need(1, 1, age=30), NOW) == pytest.approx(0.5)
    assert need_freshness(need(1, 1, age=90), NOW) < 0.2


def test_category_match_uses_share_of_open_quantity():
    open_needs = [need(1, 1, "Food", qty=50), need(2, 1, "Hygiene", qty=50)]
    assert category_match_score(open_needs, "food") == pytest.approx(0.75)
    assert category_match_score(open_needs, "Bedding") == 0.0


def test_is_open_historical_window():
    n = need(1, 1, age=10, status="Fulfilled", pledged=NOW - timedelta(days=2))
    assert is_open(n, NOW - timedelta(days=5), live=False)
    assert is_open(n, NOW - timedelta(days=2), live=False)  # still a candidate at the moment it was chosen
    assert not is_open(n, NOW, live=False)
    assert not is_open(n, NOW - timedelta(days=11), live=False)
    assert not is_open(n, NOW, live=True)


def test_new_donor_profile_is_only_the_requested_category():
    needs = [need(1, 1, "Food"), need(2, 2, "Hygiene", "Soap Bars (Pack of 10)")]
    content = ContentModel(ContentModel.fit_vectorizer(needs), needs)
    donor = content.donor_vector([], "Food")
    assert donor.dot(content.ngo_vector([needs[0]])) > donor.dot(content.ngo_vector([needs[1]]))
    assert donor.dot(content.ngo_vector([needs[1]])) == pytest.approx(0.0)


def test_compute_candidates_only_includes_ngos_with_matching_open_needs():
    ngos = [{"id": 1, "name": "Near", "area": "Kothrud"}, {"id": 2, "name": "Far", "area": "Wagholi"},
            {"id": 3, "name": "Wrong category", "area": "Kothrud"}]
    needs = [need(1, 1, "Food"), need(2, 2, "Food"), need(3, 3, "Hygiene", "Soap Bars (Pack of 10)")]
    content = ContentModel(ContentModel.fit_vectorizer(needs), needs)
    by_ngo = {1: [needs[0]], 2: [needs[1]], 3: [needs[2]]}
    cands = compute_candidates("Food", "Kothrud", [], ngos, by_ngo, at=NOW, live=True, content=content)
    assert {c["ngo"]["id"] for c in cands} == {1, 2}
    near, far = sorted(cands, key=lambda c: c["ngo"]["id"])
    assert near["features"]["proximity"] > far["features"]["proximity"]

    fallback = compute_candidates("Medical", "Kothrud", [], ngos, by_ngo, at=NOW, live=True, content=content,
                                  allow_fallback=True)
    assert len(fallback) == 3 and all(c["features"]["category_match"] == 0 for c in fallback)
