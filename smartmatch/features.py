"""Feature engineering for SmartMatch, kept free of Flask/DB so it can be unit tested.

Everything works on plain dicts:
  ngo:  {id, name, area}
  need: {id, orphanage_id, item_name, category, quantity, is_urgent, status,
         date_posted, pledge_timestamp, last_updated, is_stale, donor_id}

The same code builds features for live recommendations (live=True, "open" means
status == 'Active') and for training on history (live=False, "open" means the
listing was posted before the event and not yet closed at that moment). Sharing it
keeps training and serving consistent.
"""
import math
import re
from collections import Counter
from datetime import timedelta

import numpy as np

from .catalog import same_category
from .geo import area_distance_km

# Features the learned ranker sees. Freshness is deliberately not one of them: the
# history only records a listing's final last_updated, so it cannot be learned from
# past behaviour. It is applied as a business rule on top (see final_score).
FEATURES = ["category_match", "content_sim", "proximity", "affinity", "urgency"]
DEFAULT_WEIGHTS = {
    "category_match": 0.30,
    "content_sim": 0.20,
    "proximity": 0.30,
    "affinity": 0.12,
    "urgency": 0.08,
}
PROXIMITY_SCALE_KM = 6.0
NEUTRAL_PROXIMITY = 0.5
PROFILE_WINDOW_DAYS = 365
STALE_AFTER_DAYS = 30


def category_token(category):
    return "cat_" + re.sub(r"[^a-z0-9]+", "_", str(category).strip().lower()).strip("_")


def need_doc(need):
    return f"{category_token(need['category'])} {need['item_name']}"


def is_open(need, at, live):
    if live:
        return need["status"] == "Active"
    posted = need["date_posted"]
    if posted is None or posted >= at:
        return False
    closed = need["pledge_timestamp"]
    if closed is not None:
        # >= so the listing a donor chose is still a candidate at the moment they chose it.
        return closed >= at
    return need["status"] == "Active"


def proximity_score(distance_km):
    if distance_km is None:
        return NEUTRAL_PROXIMITY
    return math.exp(-distance_km / PROXIMITY_SCALE_KM)


def need_age_days(need, now):
    stamp = need.get("last_updated") or need.get("date_posted")
    if stamp is None:
        return 0.0
    return max(0.0, (now - stamp).total_seconds() / 86400.0)


def need_freshness(need, now):
    """1.0 for recently confirmed listings, 0.5 at the 30-day stale line, decaying towards 0.1."""
    age = need_age_days(need, now)
    if age <= 14:
        score = 1.0
    elif age <= STALE_AFTER_DAYS:
        score = 1.0 - 0.5 * (age - 14) / (STALE_AFTER_DAYS - 14)
    else:
        score = max(0.1, 0.5 * math.exp(-(age - STALE_AFTER_DAYS) / 30.0))
    if need.get("is_stale"):
        score = min(score, 0.5)
    return score


def affinity_score(past_gifts):
    return min(1.0, math.log1p(past_gifts) / math.log1p(5))


def category_match_score(open_needs, category):
    """0 if the NGO has no open need in the category, else 0.5-1.0 by the category's share of its open quantity."""
    matching = [n for n in open_needs if same_category(n["category"], category)]
    if not matching:
        return 0.0
    total = sum(max(1, n["quantity"] or 1) for n in open_needs)
    share = sum(max(1, n["quantity"] or 1) for n in matching) / total
    return 0.5 + 0.5 * share


def default_base_score(features):
    total = sum(DEFAULT_WEIGHTS.values())
    return sum(DEFAULT_WEIGHTS[name] * features[name] for name in FEATURES) / total


def final_score(base, freshness):
    """Down-weight NGOs whose matching listings have not been confirmed recently (at most halves the score)."""
    return base * (0.5 + 0.5 * freshness)


def feature_vector(features):
    return [features[name] for name in FEATURES]


class ContentModel:
    """TF-IDF vectors for listings, donor profiles and NGO profiles.

    Vectors are dense because the vocabulary (category tokens plus item-name words) is
    only a few hundred terms.
    """

    def __init__(self, vectorizer, needs):
        self.vectorizer = vectorizer
        self.row_by_id = {n["id"]: i for i, n in enumerate(needs)}
        self.matrix = vectorizer.transform([need_doc(n) for n in needs]).toarray() if needs else np.zeros((0, 0))

    @staticmethod
    def fit_vectorizer(needs):
        from sklearn.feature_extraction.text import TfidfVectorizer

        docs = [need_doc(n) for n in needs] or ["cat_food"]
        return TfidfVectorizer(sublinear_tf=True).fit(docs)

    def _profile(self, needs):
        rows = [self.row_by_id[n["id"]] for n in needs if n["id"] in self.row_by_id]
        if not rows:
            return None
        return _unit(self.matrix[rows].sum(axis=0))

    def category_vector(self, category):
        words = re.sub(r"[^a-z0-9]+", " ", str(category).lower())
        return _unit(self.vectorizer.transform([f"{category_token(category)} {words}"]).toarray()[0])

    def donor_vector(self, history, category):
        requested = self.category_vector(category)
        past = self._profile(history)
        if past is None:
            return requested
        return _unit(0.6 * past + 0.4 * requested)

    def ngo_vector(self, needs):
        return self._profile(needs)


def _unit(vec):
    vec = np.asarray(vec, dtype=float).ravel()
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def compute_candidates(category, donor_area, donor_history, ngos, needs_by_ngo, at, live, content,
                       allow_fallback=False):
    """Build a feature row for every NGO that could receive this donation.

    donor_history must only contain the donor's gifts before `at`.
    Returns a list of dicts: {ngo, features, freshness, distance_km, matching_needs}.
    """
    open_by_ngo = {}
    for ngo in ngos:
        open_needs = [n for n in needs_by_ngo.get(ngo["id"], []) if is_open(n, at, live)]
        if open_needs:
            open_by_ngo[ngo["id"]] = open_needs

    def matching(ngo_id):
        return [n for n in open_by_ngo.get(ngo_id, []) if same_category(n["category"], category)]

    pool = [ngo for ngo in ngos if matching(ngo["id"])]
    if not pool and allow_fallback:
        pool = [ngo for ngo in ngos if ngo["id"] in open_by_ngo]

    donor_vec = content.donor_vector(donor_history, category)
    gifts = Counter(h["orphanage_id"] for h in donor_history)
    window_start = at - timedelta(days=PROFILE_WINDOW_DAYS)

    candidates = []
    for ngo in pool:
        open_needs = open_by_ngo[ngo["id"]]
        matched = matching(ngo["id"])
        profile_needs = [
            n for n in needs_by_ngo[ngo["id"]]
            if n["date_posted"] is not None and window_start <= n["date_posted"] < at
        ] or open_needs
        ngo_vec = content.ngo_vector(profile_needs)
        content_sim = float(np.dot(donor_vec, ngo_vec)) if ngo_vec is not None else 0.0
        distance = area_distance_km(donor_area, ngo["area"])
        features = {
            "category_match": category_match_score(open_needs, category),
            "content_sim": max(0.0, min(1.0, content_sim)),
            "proximity": proximity_score(distance),
            "affinity": affinity_score(gifts.get(ngo["id"], 0)),
            "urgency": 1.0 if any(n["is_urgent"] for n in matched) else 0.0,
        }
        # An NGO is as fresh as its best relevant listing; a donor only needs one live need.
        freshness = max(need_freshness(n, at) for n in (matched or open_needs)) if live else 1.0
        candidates.append({
            "ngo": ngo,
            "features": features,
            "freshness": freshness,
            "distance_km": distance,
            "matching_needs": matched,
        })
    return candidates
