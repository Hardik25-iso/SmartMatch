from app import ItemNeeded
from smartmatch.freshness import freshness_summary, get_stale_listings, scan_stale_listings
from tests.conftest import login


def test_scan_flags_only_active_listings_older_than_30_days(db, factory):
    ngo = factory.ngo()
    old = factory.need(ngo, age_days=31)
    recent = factory.need(ngo, age_days=29)
    pledged = factory.need(ngo, age_days=60, status="Pledged")
    fulfilled = factory.need(ngo, age_days=60, status="Fulfilled")
    old_stamp = old.last_updated

    result = scan_stale_listings()
    db.session.expire_all()

    assert result["newly_stale"] == 1
    assert db.session.get(ItemNeeded, old.id).is_stale
    assert db.session.get(ItemNeeded, old.id).stale_since is not None
    assert not any(db.session.get(ItemNeeded, i.id).is_stale for i in (recent, pledged, fulfilled))
    # Flagging must not look like the NGO refreshed the listing.
    assert db.session.get(ItemNeeded, old.id).last_updated == old_stamp

    again = scan_stale_listings()
    assert again["newly_stale"] == 0 and again["still_stale"] == 1


def test_scan_recovers_listings_that_were_updated_or_closed(db, factory):
    ngo = factory.ngo()
    a = factory.need(ngo, age_days=45)
    b = factory.need(ngo, age_days=45)
    scan_stale_listings()
    db.session.expire_all()

    a = db.session.get(ItemNeeded, a.id)
    a.last_updated = factory.now
    db.session.get(ItemNeeded, b.id).status = "Fulfilled"
    db.session.commit()

    assert scan_stale_listings()["recovered"] == 2


def test_stale_listings_and_summary(db, factory):
    ngo = factory.ngo(name="Asha Home", area="Baner")
    factory.need(ngo, age_days=70, item_name="Blankets (Single)", category="Bedding")
    factory.need(ngo, age_days=5)
    scan_stale_listings()

    listings = get_stale_listings()
    assert len(listings) == 1
    assert listings[0]["orphanage_name"] == "Asha Home" and listings[0]["age_days"] == 70
    assert listings[0]["last_alert_at"] is None

    summary = freshness_summary()
    assert summary["active"] == 2 and summary["stale"] == 1 and summary["pct_fresh"] == 50.0
    assert [b["count"] for b in summary["buckets"]] == [1, 0, 0, 1]


def test_ngo_still_needed_button_clears_the_flag(client, db, factory):
    ngo = factory.ngo()
    item = factory.need(ngo, age_days=40)
    scan_stale_listings()

    other = factory.ngo()
    login(client, other.account)
    assert client.post(f"/smartmatch/listings/{item.id}/refresh").status_code == 403
    client.get("/logout")

    login(client, ngo.account)
    assert client.get("/dashboard").get_data(as_text=True).count("Still needed") == 1
    assert client.post(f"/smartmatch/listings/{item.id}/refresh").status_code == 302
    db.session.expire_all()
    refreshed = db.session.get(ItemNeeded, item.id)
    assert not refreshed.is_stale and refreshed.last_updated >= factory.now
    assert get_stale_listings() == []
