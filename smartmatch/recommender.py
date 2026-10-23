"""Donor-to-NGO recommendations: loads SmartMatch data, scores candidates, trains the ranker."""
import os
import random
from collections import defaultdict
from datetime import datetime

import numpy as np

from . import ctx
from .catalog import normalize_category
from .features import (
    DEFAULT_WEIGHTS, FEATURES, ContentModel, compute_candidates, default_base_score,
    feature_vector, final_score,
)
from .geo import normalize_area

MODEL_FILENAME = "smartmatch_model.joblib"
_model_cache = {"path": None, "mtime": None, "bundle": None}


class RecommendationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# --- Data loading -------------------------------------------------------------

def load_snapshot():
    """All NGOs and listings as plain dicts (a few thousand rows at most)."""
    m = ctx.models
    ngos = [
        {"id": o.id, "name": o.name, "area": normalize_area(o.city) or o.city}
        for o in m.Orphanage.query.order_by(m.Orphanage.id).all()
    ]
    rows = ctx.db.session.query(
        m.ItemNeeded.id, m.ItemNeeded.orphanage_id, m.ItemNeeded.item_name, m.ItemNeeded.category,
        m.ItemNeeded.quantity_needed, m.ItemNeeded.is_urgent, m.ItemNeeded.status,
        m.ItemNeeded.date_posted, m.ItemNeeded.pledge_timestamp, m.ItemNeeded.last_updated,
        m.ItemNeeded.is_stale, m.ItemNeeded.donor_id,
    ).all()
    needs = [
        {
            "id": r.id, "orphanage_id": r.orphanage_id, "item_name": r.item_name, "category": r.category,
            "quantity": r.quantity_needed, "is_urgent": bool(r.is_urgent), "status": r.status,
            "date_posted": r.date_posted, "pledge_timestamp": r.pledge_timestamp,
            "last_updated": r.last_updated, "is_stale": bool(r.is_stale), "donor_id": r.donor_id,
        }
        for r in rows
    ]
    return ngos, needs


def group_by_ngo(needs):
    grouped = defaultdict(list)
    for n in needs:
        grouped[n["orphanage_id"]].append(n)
    return grouped


def donation_history(needs, donor_id, before=None):
    gifts = [
        n for n in needs
        if n["donor_id"] == donor_id and n["pledge_timestamp"] is not None
        and n["status"] in ("Pledged", "Fulfilled")
        and (before is None or n["pledge_timestamp"] < before)
    ]
    return sorted(gifts, key=lambda n: n["pledge_timestamp"])


# --- Model storage ------------------------------------------------------------

def model_path():
    return os.environ.get("SMARTMATCH_MODEL_PATH") or os.path.join(ctx.app.instance_path, MODEL_FILENAME)


def load_model():
    """The trained bundle, or None. Reloaded when the file changes so every gunicorn worker sees retrains."""
    path = model_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _model_cache.update(path=path, mtime=None, bundle=None)
        return None
    if _model_cache["path"] != path or _model_cache["mtime"] != mtime:
        import joblib

        try:
            bundle = joblib.load(path)
        except Exception as e:  # a corrupt or incompatible file should not break recommendations
            ctx.app.logger.warning("SmartMatch: could not load model %s: %s", path, e)
            bundle = None
        _model_cache.update(path=path, mtime=mtime, bundle=bundle)
    return _model_cache["bundle"]


def model_info(bundle=None):
    bundle = bundle if bundle is not None else load_model()
    if not bundle:
        return {"type": "default_weights", "weights": DEFAULT_WEIGHTS, "trained_at": None, "metrics": None}
    return {
        "type": "pairwise_logistic_regression",
        "trained_at": bundle["trained_at"],
        "coefficients": bundle["coefficients"],
        "metrics": bundle["metrics"],
        "n_train_pairs": bundle["n_train_pairs"],
    }


# --- Scoring ------------------------------------------------------------------

def score_candidates(candidates, bundle):
    if not candidates:
        return
    for c in candidates:
        if bundle:
            c["base_score"] = learned_base_score(c["features"], bundle["weights"])
        else:
            c["base_score"] = default_base_score(c["features"])
        c["score"] = final_score(c["base_score"], c["freshness"])


def recommend(donor_id, item_category, donor_area=None, limit=10, now=None):
    m = ctx.models
    donor = ctx.db.session.get(m.Account, donor_id)
    if donor is None or donor.role != "donor":
        raise RecommendationError(f"Donor {donor_id} not found.", status=404)
    category = normalize_category(item_category)
    if not category:
        raise RecommendationError("item_category is required.")
    if donor_area:
        area = normalize_area(donor_area)
        if area is None:
            raise RecommendationError(f"Unknown donor_area '{donor_area}'.")
    else:
        area = normalize_area(donor.area)

    now = now or datetime.utcnow()
    ngos, needs = load_snapshot()
    bundle = load_model()
    vectorizer = bundle["vectorizer"] if bundle else ContentModel.fit_vectorizer(needs)
    content = ContentModel(vectorizer, needs)
    history = donation_history(needs, donor.id)

    candidates = compute_candidates(
        category, area, history, ngos, group_by_ngo(needs), at=now, live=True,
        content=content, allow_fallback=True,
    )
    score_candidates(candidates, bundle)
    candidates.sort(key=lambda c: c["score"], reverse=True)

    results = []
    for rank, c in enumerate(candidates[:limit], start=1):
        results.append({
            "rank": rank,
            "orphanage_id": c["ngo"]["id"],
            "orphanage_name": c["ngo"]["name"],
            "area": c["ngo"]["area"],
            "distance_km": round(c["distance_km"], 1) if c["distance_km"] is not None else None,
            "score": round(c["score"], 4),
            "base_score": round(c["base_score"], 4),
            "freshness": round(c["freshness"], 3),
            "components": {k: round(v, 3) for k, v in c["features"].items()},
            "matching_needs": [
                {
                    "item_id": n["id"], "item_name": n["item_name"], "category": n["category"],
                    "quantity_needed": n["quantity"], "is_urgent": n["is_urgent"], "is_stale": n["is_stale"],
                    "last_updated": n["last_updated"].isoformat() if n["last_updated"] else None,
                }
                for n in c["matching_needs"]
            ],
        })
    return {
        "query": {"donor_id": donor.id, "donor_name": donor.name, "item_category": category, "donor_area": area},
        "model": model_info(bundle),
        "category_has_matches": any(c["matching_needs"] for c in candidates),
        "history": [
            {"item_name": h["item_name"], "category": h["category"], "orphanage_id": h["orphanage_id"],
             "pledge_timestamp": h["pledge_timestamp"].isoformat()}
            for h in reversed(history[-10:])
        ],
        "results": results,
    }


# --- Training -----------------------------------------------------------------

def _rank_metrics(cases, score_fn, k=5):
    hits, reciprocal = 0, 0.0
    for candidates, chosen_id in cases:
        ranked = sorted(candidates, key=score_fn, reverse=True)
        position = next(i for i, c in enumerate(ranked, start=1) if c["ngo"]["id"] == chosen_id)
        hits += position <= k
        reciprocal += 1.0 / position
    n = len(cases) or 1
    return {f"hit_rate@{k}": round(hits / n, 4), "mrr": round(reciprocal / n, 4)}


def learned_base_score(features, weights):
    """Weighted sum with the learned weights, scaled to 0-1 like the default-weight score."""
    positive = sum(w for w in weights.values() if w > 0) or 1.0
    return max(0.0, min(1.0, sum(weights[name] * features[name] for name in FEATURES) / positive))


def train_model(holdout_frac=0.2, negatives=6, seed=42, save=True, log=print):
    """Learn feature weights from donation history (pairwise logistic regression) and evaluate them.

    For each past gift, the NGO the donor chose is paired with NGOs they could have
    chosen instead. The model learns weights w such that w·(chosen - other) > 0, which
    optimises ranking directly (the RankNet/RankSVM idea) rather than per-NGO probability.

    Every event's features use only what was known at that moment (earlier gifts,
    listings open then), so there is no leakage from the future.
    """
    from sklearn.linear_model import LogisticRegression

    rng = random.Random(seed)
    ngos, needs = load_snapshot()
    donors = {a.id: normalize_area(a.area) for a in ctx.models.Account.query.filter_by(role="donor")}
    events = sorted(
        (n for n in needs if n["donor_id"] in donors and n["pledge_timestamp"] is not None
         and n["status"] in ("Pledged", "Fulfilled")),
        key=lambda n: n["pledge_timestamp"],
    )
    if len(events) < 50:
        raise RecommendationError(f"Need at least 50 donation events to train, found {len(events)}. "
                                  "Run `flask smartmatch-seed` first.")

    vectorizer = ContentModel.fit_vectorizer(needs)
    content = ContentModel(vectorizer, needs)
    needs_by_ngo = group_by_ngo(needs)
    split = int(len(events) * (1 - holdout_frac))
    history = defaultdict(list)
    X, y, eval_cases = [], [], []

    for i, event in enumerate(events):
        donor_id = event["donor_id"]
        candidates = compute_candidates(
            event["category"], donors[donor_id], history[donor_id], ngos, needs_by_ngo,
            at=event["pledge_timestamp"], live=False, content=content,
        )
        chosen = [c for c in candidates if c["ngo"]["id"] == event["orphanage_id"]]
        if chosen and len(candidates) >= 2:
            if i < split:
                positive = np.array(feature_vector(chosen[0]["features"]))
                others = [c for c in candidates if c["ngo"]["id"] != event["orphanage_id"]]
                for c in rng.sample(others, min(negatives, len(others))):
                    diff = positive - np.array(feature_vector(c["features"]))
                    X.extend([diff, -diff])  # symmetric pairs keep the classes balanced
                    y.extend([1, 0])
            else:
                eval_cases.append((candidates, event["orphanage_id"]))
        history[donor_id].append(event)

    if len(set(y)) < 2:
        raise RecommendationError("Not enough candidate diversity in history to train a ranker.")

    model = LogisticRegression(fit_intercept=False, C=1.0, max_iter=1000)
    model.fit(np.array(X), np.array(y))
    weights = {name: float(w) for name, w in zip(FEATURES, model.coef_[0])}

    def model_score(c):
        return learned_base_score(c["features"], weights)

    metrics = {
        "holdout_events": len(eval_cases),
        "avg_candidates": round(float(np.mean([len(c) for c, _ in eval_cases])), 1) if eval_cases else 0,
        "learned": _rank_metrics(eval_cases, model_score),
        "default_weights": _rank_metrics(eval_cases, lambda c: default_base_score(c["features"])),
        "nearest_ngo": _rank_metrics(eval_cases, lambda c: c["features"]["proximity"]),
    }
    bundle = {
        "model": model,
        "weights": weights,
        "vectorizer": vectorizer,
        "features": FEATURES,
        "coefficients": {name: round(w, 4) for name, w in weights.items()},
        "trained_at": datetime.utcnow().isoformat(timespec="seconds"),
        "n_train_pairs": len(y) // 2,
        "metrics": metrics,
    }
    if save:
        import joblib

        path = model_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        joblib.dump(bundle, tmp)
        os.replace(tmp, path)  # atomic, so workers never load a half-written file
        log(f"Saved model to {path}")
    return bundle
