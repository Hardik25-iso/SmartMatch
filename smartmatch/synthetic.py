"""Synthetic Pune donors, NGOs and donation history for SmartMatch.

It simulates day by day, so the history has the structure the recommender should
learn:
  * NGOs post needs in their focus categories and keep 2-6 listings open.
  * Donors give mostly in the categories they prefer, to NGOs within their
    travel tolerance, often returning to NGOs they gave to before, with a
    preference for urgent needs.
  * Diligent NGOs re-confirm open listings every couple of weeks; neglectful
    ones don't, so their listings drift past the 30-day stale line.

Existing SmartMatch rows (the seed-db NGOs and demo donor) are kept and join the
simulation.
"""
import math
import random
from datetime import datetime, timedelta

from . import ctx
from .catalog import CATEGORIES, ITEMS_BY_CATEGORY
from .geo import AREA_NAMES, normalize_area

NGO_NAME_PARTS = (
    ["Asha", "Snehalaya", "Aadhar", "Umeed", "Prerna", "Jeevan", "Aanchal", "Saksham", "Kilbil",
     "Sevadham", "Anand", "Pratham", "Sahyog", "Navjeevan", "Mauli", "Gokul", "Shanti", "Disha"],
    ["Bal Ashram", "Children's Home", "Foundation", "Trust", "Seva Sangh", "Balgram", "Care Centre",
     "Sanstha", "Shelter", "Kids Home"],
)
FIRST_NAMES = ["Aarav", "Ananya", "Rohan", "Priya", "Siddharth", "Sneha", "Vikram", "Neha", "Aditya", "Pooja",
               "Omkar", "Sakshi", "Tanmay", "Gauri", "Kunal", "Mrunal", "Yash", "Isha", "Harsh", "Rutuja",
               "Nikhil", "Shruti", "Pranav", "Aditi", "Ketan", "Vaishnavi", "Amit", "Kavya", "Sanket", "Riya"]
LAST_NAMES = ["Kulkarni", "Deshpande", "Patil", "Joshi", "Shinde", "Pawar", "Gokhale", "Jadhav", "Bhosale",
              "Apte", "Mehta", "Shah", "Agarwal", "Iyer", "Nair", "Chavan", "Kale", "Sathe", "Gupta", "Rao"]
# Residential hubs get more donors.
DONOR_AREA_WEIGHTS = {"Kothrud": 3, "Baner": 3, "Aundh": 2.5, "Wakad": 2.5, "Hinjewadi": 2, "Viman Nagar": 2.5,
                      "Kharadi": 2.5, "Hadapsar": 2, "Magarpatta": 2, "Koregaon Park": 2, "Pimpri": 1.5,
                      "Chinchwad": 1.5, "Sinhagad Road": 1.5, "Kondhwa": 1.5}

SIM_DAYS = 540
PLEDGE_WINDOW_DAYS = 14   # gifts newer than this are still 'Pledged' (awaiting delivery)
SYNTHETIC_PASSWORD = "11"


def _dirichlet(rng, alphas):
    draws = [rng.gammavariate(a, 1.0) for a in alphas]
    total = sum(draws)
    return [d / total for d in draws]


def _weighted_choice(rng, items, weights):
    return rng.choices(items, weights=weights, k=1)[0]


def _focus_profile(rng, preset=None):
    """Weights over CATEGORIES: 1-3 focus categories dominate, the rest get a small share."""
    focus = list(preset) if preset else rng.sample(CATEGORIES, rng.randint(1, 3))
    return {c: (rng.uniform(3, 6) if c in focus else 0.25) for c in CATEGORIES}


def generate(n_donors=150, n_ngos=30, n_donations=1200, seed=42, log=print):
    """Add synthetic data to the database. Returns a summary dict."""
    rng = random.Random(seed)
    db, m = ctx.db, ctx.models
    now = datetime.utcnow().replace(microsecond=0)
    start = now - timedelta(days=SIM_DAYS)
    password_hash = _password_hash()

    # --- NGOs: existing seed-db orphanages plus new synthetic ones ---------------
    ngo_state = []
    for o in m.Orphanage.query.all():
        preset = {n.category for n in o.items} & set(CATEGORIES)
        if not o.contact_phone:
            o.contact_phone = f"+919000{o.id:06d}"
        ngo_state.append(_ngo_state(rng, o, preset))

    used_names = {s["orphanage"].name for s in ngo_state}
    for i in range(n_ngos):
        name = _unique_ngo_name(rng, used_names)
        area = rng.choice(AREA_NAMES)
        email = f"ngo{i + 1}@smartmatch.test"
        contact = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        account = m.Account(email=email, name=contact, password=password_hash, role="ngo")
        orphanage = m.Orphanage(
            name=name, city=area, address=f"{rng.randint(1, 250)}, {area}, Pune, Maharashtra",
            contact_email=email, contact_phone=f"+919100{i + 1:06d}",
            description=f"{name} supports children in and around {area}, Pune.",
            account=account, children_0_5=rng.randint(2, 12), children_6_12=rng.randint(8, 30),
            children_13_18=rng.randint(4, 20),
        )
        db.session.add_all([account, orphanage])
        ngo_state.append(_ngo_state(rng, orphanage, None))
    db.session.flush()  # assign ids

    # --- Donors ----------------------------------------------------------------------
    donor_state = []
    area_weights = [DONOR_AREA_WEIGHTS.get(a, 1.0) for a in AREA_NAMES]
    for account in m.Account.query.filter_by(role="donor").all():
        if not normalize_area(account.area):
            account.area = _weighted_choice(rng, AREA_NAMES, area_weights)
        donor_state.append(_donor_state(rng, account))
    for i in range(n_donors):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        account = m.Account(
            email=f"donor{i + 1}@smartmatch.test", name=f"{first} {last}", password=password_hash,
            role="donor", area=_weighted_choice(rng, AREA_NAMES, area_weights),
        )
        db.session.add(account)
        donor_state.append(_donor_state(rng, account))
    db.session.flush()

    # --- Day-by-day simulation -------------------------------------------------------------
    listings = []          # every ItemNeeded created, as dicts until the final insert
    open_by_cat = {c: [] for c in CATEGORIES}
    per_day = n_donations / SIM_DAYS
    activity = [d["activity"] for d in donor_state]
    gifts = 0

    for day in range(SIM_DAYS):  # ends yesterday, so no timestamp lands in the future
        today = start + timedelta(days=day)
        for ngo in ngo_state:
            _ngo_daily(rng, ngo, today, listings, open_by_cat)

        for _ in range(_poisson(rng, per_day)):
            donor = _weighted_choice(rng, donor_state, activity)
            at = today + timedelta(hours=rng.uniform(8, 20))
            if _donate(rng, donor, at, open_by_cat):
                gifts += 1

    # Needs from the original seed-db stay as they are; everything simulated is inserted now.
    for listing in listings:
        closed = listing["closed_at"]
        if listing["donor"] is not None:
            status = "Pledged" if (now - closed).days < PLEDGE_WINDOW_DAYS else "Fulfilled"
        elif closed is not None:
            status = "Fulfilled"   # met offline, outside SmartMatch
        else:
            status = "Active"
        db.session.add(m.ItemNeeded(
            item_name=listing["item_name"], category=listing["category"], quantity_needed=listing["quantity"],
            is_urgent=listing["is_urgent"], date_posted=listing["posted_at"], status=status,
            orphanage_id=listing["ngo"]["orphanage"].id,
            donor_id=listing["donor"]["account"].id if listing["donor"] else None,
            pledge_timestamp=closed, last_updated=listing["last_updated"], is_stale=False,
        ))

    for ngo in ngo_state:
        for _ in range(rng.randint(0, 4)):
            db.session.add(m.Update(
                title=rng.choice(["Thank you, donors!", "Monsoon drive update", "New admissions this month",
                                  "School results are in", "Festival celebrations"]),
                body=f"An update from {ngo['orphanage'].name}.",
                date_posted=now - timedelta(days=rng.randint(0, 120)),
                orphanage=ngo["orphanage"],
            ))

    db.session.commit()
    active = [l for l in listings if l["closed_at"] is None]
    old = [l for l in active if (now - l["last_updated"]).days > 30]
    summary = {
        "ngos_added": n_ngos, "donors_added": n_donors, "listings": len(listings),
        "donations": gifts, "active_listings": len(active), "older_than_30_days": len(old),
    }
    log("SmartMatch synthetic data: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    return summary


def _password_hash():
    # One bcrypt hash shared by every synthetic account: hashing ~180 passwords would take about a minute.
    from flask_bcrypt import generate_password_hash
    return generate_password_hash(SYNTHETIC_PASSWORD).decode("utf-8")


def _unique_ngo_name(rng, used):
    for _ in range(100):
        name = f"{rng.choice(NGO_NAME_PARTS[0])} {rng.choice(NGO_NAME_PARTS[1])}"
        if name not in used:
            used.add(name)
            return name
    name = f"SmartMatch Partner Home {len(used) + 1}"
    used.add(name)
    return name


def _ngo_state(rng, orphanage, preset):
    return {
        "orphanage": orphanage,
        "area": normalize_area(orphanage.city),
        "focus": _focus_profile(rng, preset),
        "target_open": rng.randint(2, 6),
        "open": [],
        # Half the NGOs never re-confirm their listings; the rest do every 7-20 days.
        "refresh_every": None if rng.random() < 0.5 else rng.randint(7, 20),
        "next_refresh": rng.randint(0, 20),
        "max_open_days": rng.randint(60, 150),
    }


def _donor_state(rng, account):
    prefs = _dirichlet(rng, [0.35] * len(CATEGORIES))
    return {
        "account": account,
        "area": normalize_area(account.area),
        "prefs": prefs,
        "tolerance_km": rng.uniform(3, 15),
        "loyalty": rng.uniform(0.2, 1.5),
        "activity": rng.lognormvariate(0, 0.8),
        "gifts": {},
    }


def _ngo_daily(rng, ngo, today, listings, open_by_cat):
    # Post new needs until the NGO has its target number of open listings.
    if len(ngo["open"]) < ngo["target_open"] and rng.random() < 0.35:
        category = _weighted_choice(rng, CATEGORIES, [ngo["focus"][c] for c in CATEGORIES])
        posted = today + timedelta(hours=rng.uniform(7, 19))
        listing = {
            "ngo": ngo, "category": category, "item_name": rng.choice(ITEMS_BY_CATEGORY[category]),
            "quantity": rng.choice([10, 20, 25, 30, 40, 50, 75, 100, 150, 200]),
            "is_urgent": rng.random() < 0.2, "posted_at": posted, "last_updated": posted,
            "closed_at": None, "donor": None,
        }
        listings.append(listing)
        ngo["open"].append(listing)
        open_by_cat[category].append(listing)

    # Diligent NGOs re-confirm their open listings periodically.
    if ngo["refresh_every"]:
        ngo["next_refresh"] -= 1
        if ngo["next_refresh"] <= 0:
            for listing in ngo["open"]:
                listing["last_updated"] = max(listing["last_updated"], today + timedelta(hours=10))
            ngo["next_refresh"] = ngo["refresh_every"]

    # Some needs are met outside the platform after a while.
    for listing in list(ngo["open"]):
        if (today - listing["posted_at"]).days > ngo["max_open_days"] and rng.random() < 0.05:
            _close(listing, today + timedelta(hours=12), None, open_by_cat)


def _donate(rng, donor, at, open_by_cat):
    if rng.random() < 0.85:
        category = _weighted_choice(rng, CATEGORIES, donor["prefs"])
    else:
        category = rng.choice(CATEGORIES)
    options = [l for l in open_by_cat[category] if l["posted_at"] < at]
    if not options:
        return False

    weights = []
    for listing in options:
        ngo = listing["ngo"]
        distance = _distance(donor["area"], ngo["area"])
        w = math.exp(-distance / donor["tolerance_km"])
        w *= 1 + donor["loyalty"] * donor["gifts"].get(id(ngo), 0)
        w *= 1.6 if listing["is_urgent"] else 1.0
        w *= 0.5 + ngo["focus"][category] / 6
        weights.append(w)
    listing = _weighted_choice(rng, options, weights)
    _close(listing, at, donor, open_by_cat)
    donor["gifts"][id(listing["ngo"])] = donor["gifts"].get(id(listing["ngo"]), 0) + 1
    return True


def _close(listing, at, donor, open_by_cat):
    listing["closed_at"] = at
    listing["donor"] = donor
    listing["ngo"]["open"].remove(listing)
    open_by_cat[listing["category"]].remove(listing)


_distance_cache = {}


def _distance(a, b):
    from .geo import area_distance_km
    key = (a, b)
    if key not in _distance_cache:
        d = area_distance_km(a, b)
        _distance_cache[key] = 10.0 if d is None else d
    return _distance_cache[key]


def _poisson(rng, lam):
    # Knuth's method; lam is small (a few gifts per day).
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1
