import pytest

from smartmatch.freshness import scan_stale_listings
from tests.conftest import login


@pytest.fixture
def world(factory):
    donor = factory.account(role="donor", area="Kothrud")
    near = factory.ngo(name="Near Home", area="Kothrud")
    mid = factory.ngo(name="Mid Home", area="Aundh")
    far = factory.ngo(name="Far Home", area="Wagholi")
    hygiene_only = factory.ngo(name="Hygiene Home", area="Kothrud")
    for ngo in (near, mid, far):
        factory.need(ngo, category="Food")
    factory.need(hygiene_only, category="Hygiene", item_name="Soap Bars (Pack of 10)")
    admin = factory.account(role="admin")
    return {"donor": donor, "near": near, "mid": mid, "far": far, "admin": admin}


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("SMARTMATCH_API_KEY", "test-key")
    return {"X-API-Key": "test-key"}


def test_api_requires_key_or_admin(client, world, monkeypatch):
    monkeypatch.delenv("SMARTMATCH_API_KEY", raising=False)
    assert client.get("/api/stale-listings").status_code == 401

    monkeypatch.setenv("SMARTMATCH_API_KEY", "right")
    assert client.get("/api/stale-listings", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/stale-listings", headers={"X-API-Key": "right"}).status_code == 200

    monkeypatch.delenv("SMARTMATCH_API_KEY")
    login(client, world["admin"])
    assert client.get("/api/stale-listings").status_code == 200


def test_recommend_ranks_matching_ngos_by_proximity(client, world, api_key):
    res = client.get(f"/api/recommend?donor_id={world['donor'].id}&item_category=food", headers=api_key)
    assert res.status_code == 200
    body = res.get_json()
    names = [r["orphanage_name"] for r in body["results"]]
    assert names == ["Near Home", "Mid Home", "Far Home"]  # Hygiene-only NGO is excluded
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert body["query"] == {"donor_id": world["donor"].id, "donor_name": world["donor"].name,
                             "item_category": "Food", "donor_area": "Kothrud"}
    assert set(body["results"][0]["components"]) >= {"category_match", "proximity", "content_sim"}
    assert body["model"]["type"] == "default_weights"


def test_recommend_area_override_changes_ranking(client, world, api_key):
    res = client.post("/api/recommend", headers=api_key,
                      json={"donor_id": world["donor"].id, "item_category": "Food", "donor_area": "Wagholi"})
    assert res.get_json()["results"][0]["orphanage_name"] == "Far Home"


def test_recommend_penalises_stale_listings(client, world, factory, api_key):
    # Same distance, same category; one NGO hasn't confirmed its listing in 90 days.
    fresh = factory.ngo(name="Fresh Twin", area="Baner")
    stale = factory.ngo(name="Stale Twin", area="Baner")
    factory.need(fresh, category="Bedding", item_name="Blankets (Single)")
    factory.need(stale, category="Bedding", item_name="Blankets (Single)", age_days=90)
    scan_stale_listings()
    body = client.get(f"/api/recommend?donor_id={world['donor'].id}&item_category=Bedding", headers=api_key).get_json()
    ranked = [r["orphanage_name"] for r in body["results"]]
    assert ranked.index("Fresh Twin") < ranked.index("Stale Twin")
    assert body["results"][ranked.index("Stale Twin")]["matching_needs"][0]["is_stale"]


def test_recommend_validation(client, world, api_key):
    assert client.get("/api/recommend?item_category=Food", headers=api_key).status_code == 400
    bad_area = client.get(f"/api/recommend?donor_id={world['donor'].id}&item_category=Food&donor_area=Mumbai",
                          headers=api_key)
    assert bad_area.status_code == 400 and "Kothrud" in bad_area.get_json()["allowed"]["donor_area"]
    assert client.get("/api/recommend?donor_id=99999&item_category=Food", headers=api_key).status_code == 404
    # An NGO account is not a donor.
    ngo_account = world["near"].account_id
    assert client.get(f"/api/recommend?donor_id={ngo_account}&item_category=Food", headers=api_key).status_code == 404


def test_stale_listings_endpoints(client, world, factory, api_key):
    factory.need(world["near"], age_days=45, item_name="Blankets (Single)", category="Bedding")
    assert client.get("/api/stale-listings", headers=api_key).get_json()["count"] == 0

    scan = client.post("/api/stale-listings/scan", headers=api_key).get_json()
    assert scan["newly_stale"] == 1

    body = client.get("/api/stale-listings", headers=api_key).get_json()
    assert body["count"] == 1 and body["listings"][0]["orphanage_name"] == "Near Home"
    other = client.get(f"/api/stale-listings?orphanage_id={world['far'].id}", headers=api_key).get_json()
    assert other["count"] == 0


def test_admin_dashboard_renders_and_is_admin_only(client, world, factory):
    factory.need(world["far"], age_days=45)
    login(client, world["donor"])
    assert client.get("/smartmatch/dashboard").status_code == 403
    client.get("/logout")

    login(client, world["admin"])
    assert client.get("/dashboard").headers["Location"].endswith("/smartmatch/dashboard")
    page = client.get(f"/smartmatch/dashboard?donor_id={world['donor'].id}&item_category=Food")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Near Home" in html and "Need-freshness health" in html

    res = client.post("/smartmatch/scan", data={"donor_id": world["donor"].id}, follow_redirects=True)
    assert "1 newly stale" in res.get_data(as_text=True)
    res = client.post("/smartmatch/alerts", follow_redirects=True)
    assert "dry run" in res.get_data(as_text=True)


def test_donor_sees_recommendations_page(client, world, factory):
    """The donor-facing page is the only place a donor meets the recommender."""
    login(client, world["donor"])
    page = client.get("/smartmatch/for-you?item_category=Food&donor_area=Kothrud")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    # Ranked, with the nearest matching NGO first and a pledge button on its need.
    assert html.index("Near Home") < html.index("Far Home")
    assert "Hygiene Home" not in html, "an NGO with no Food need should not be recommended"
    assert "match</div>" in html and "Pledge" in html
    # The donor dashboard and nav must link to it, or nobody will find it.
    assert "/smartmatch/for-you" in client.get("/dashboard").get_data(as_text=True)


def test_ngo_sees_listing_health(client, world, factory):
    from smartmatch.freshness import scan_stale_listings

    factory.need(world["near"], age_days=45)
    scan_stale_listings()
    login(client, world["near"].account)
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Listing health" in html and "ranked lower" in html


def test_for_you_is_donor_only(client, world):
    login(client, world["admin"])
    assert client.get("/smartmatch/for-you").status_code == 403
