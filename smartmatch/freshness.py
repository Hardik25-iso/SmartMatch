"""Need-freshness tracking: flag Active listings not confirmed in 30+ days as stale."""
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, update

from . import ctx

STALE_AFTER_DAYS = 30
AGE_BUCKETS = [("0-15 days", 0, 15), ("15-30 days", 15, 30), ("30-60 days", 30, 60), ("60+ days", 60, None)]


def _last_touched(ItemNeeded):
    return func.coalesce(ItemNeeded.last_updated, ItemNeeded.date_posted)


def scan_stale_listings(days=STALE_AFTER_DAYS, now=None):
    """Flag or unflag listings with bulk UPDATEs. last_updated is never written here.

    Returns {"newly_stale", "still_stale", "recovered", "cutoff"}.
    """
    db, ItemNeeded = ctx.db, ctx.models.ItemNeeded
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=days)
    touched = _last_touched(ItemNeeded)
    should_be_stale = and_(ItemNeeded.status == "Active", touched < cutoff)

    still_stale = db.session.query(func.count(ItemNeeded.id)).filter(
        should_be_stale, ItemNeeded.is_stale.is_(True)).scalar()
    newly = db.session.execute(
        update(ItemNeeded)
        .where(should_be_stale, ItemNeeded.is_stale.is_(False))
        .values(is_stale=True, stale_since=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    recovered = db.session.execute(
        update(ItemNeeded)
        .where(ItemNeeded.is_stale.is_(True), or_(ItemNeeded.status != "Active", touched >= cutoff))
        .values(is_stale=False, stale_since=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.session.commit()
    return {"newly_stale": newly, "still_stale": still_stale, "recovered": recovered,
            "cutoff": cutoff.isoformat(timespec="seconds")}


def refresh_listing(item, now=None):
    """The NGO confirmed this need is still current."""
    item.last_updated = now or datetime.utcnow()
    item.is_stale = False
    item.stale_since = None


def get_stale_listings(orphanage_id=None, now=None):
    """Listings currently flagged stale, oldest first, with each NGO's most recent alert."""
    db, m = ctx.db, ctx.models
    now = now or datetime.utcnow()
    query = db.session.query(m.ItemNeeded, m.Orphanage).join(
        m.Orphanage, m.ItemNeeded.orphanage_id == m.Orphanage.id
    ).filter(m.ItemNeeded.is_stale.is_(True), m.ItemNeeded.status == "Active")
    if orphanage_id is not None:
        query = query.filter(m.ItemNeeded.orphanage_id == orphanage_id)
    rows = query.order_by(_last_touched(m.ItemNeeded)).all()

    last_alerts = dict(
        db.session.query(m.AlertLog.orphanage_id, func.max(m.AlertLog.created_at))
        .group_by(m.AlertLog.orphanage_id).all()
    )
    listings = []
    for item, orphanage in rows:
        touched = item.last_updated or item.date_posted
        last_alert = last_alerts.get(orphanage.id)
        listings.append({
            "item_id": item.id,
            "item_name": item.item_name,
            "category": item.category,
            "quantity_needed": item.quantity_needed,
            "is_urgent": item.is_urgent,
            "orphanage_id": orphanage.id,
            "orphanage_name": orphanage.name,
            "area": orphanage.city,
            "contact_email": orphanage.contact_email,
            "last_updated": touched.isoformat(timespec="seconds") if touched else None,
            "age_days": (now - touched).days if touched else None,
            "stale_since": item.stale_since.isoformat(timespec="seconds") if item.stale_since else None,
            "last_alert_at": last_alert.isoformat(timespec="seconds") if last_alert else None,
        })
    return listings


def freshness_summary(now=None):
    """Counts for the dashboard: active, stale, % fresh and age buckets of Active listings."""
    db, ItemNeeded = ctx.db, ctx.models.ItemNeeded
    now = now or datetime.utcnow()
    stamps = [
        row[0] for row in db.session.query(_last_touched(ItemNeeded))
        .filter(ItemNeeded.status == "Active").all()
    ]
    stale = db.session.query(func.count(ItemNeeded.id)).filter(
        ItemNeeded.status == "Active", ItemNeeded.is_stale.is_(True)).scalar()
    ages = [(now - s).total_seconds() / 86400 if s else 0 for s in stamps]
    buckets = []
    for label, low, high in AGE_BUCKETS:
        count = sum(1 for a in ages if a >= low and (high is None or a < high))
        buckets.append({"label": label, "count": count,
                        "pct": round(100 * count / len(ages), 1) if ages else 0.0,
                        "stale_zone": low >= STALE_AFTER_DAYS})
    active = len(ages)
    return {
        "active": active,
        "stale": stale,
        "pct_fresh": round(100 * (active - stale) / active, 1) if active else 100.0,
        "buckets": buckets,
    }
