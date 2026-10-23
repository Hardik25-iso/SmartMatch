"""SmartMatch REST API: /api/recommend and /api/stale-listings."""
import hmac
import os
from functools import wraps

from flask import Blueprint, jsonify, request
from flask_login import current_user

from .catalog import CATEGORIES
from .freshness import STALE_AFTER_DAYS, get_stale_listings, scan_stale_listings
from .geo import AREA_NAMES
from .recommender import RecommendationError, recommend

api_bp = Blueprint("smartmatch_api", __name__, url_prefix="/api")


def api_auth(view):
    """Allow a logged-in admin, or a request whose X-API-Key matches SMARTMATCH_API_KEY.

    With no key configured the API is admin-session only, so donor data is never public by accident.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user.is_authenticated and current_user.role == "admin":
            return view(*args, **kwargs)
        expected = os.environ.get("SMARTMATCH_API_KEY")
        supplied = request.headers.get("X-API-Key", "")
        if expected and hmac.compare_digest(supplied, expected):
            return view(*args, **kwargs)
        return jsonify(error="unauthorized", message="Send a valid X-API-Key header or log in as an admin."), 401
    return wrapper


def _int_arg(data, name, default=None, minimum=None, maximum=None):
    raw = data.get(name, default)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise RecommendationError(f"{name} must be an integer.")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise RecommendationError(f"{name} must be between {minimum} and {maximum}.")
    return value


@api_bp.errorhandler(RecommendationError)
def handle_recommendation_error(e):
    body = {"error": "not_found" if e.status == 404 else "bad_request", "message": str(e)}
    if e.status == 400:
        body["allowed"] = {"item_category": CATEGORIES, "donor_area": AREA_NAMES}
    return jsonify(body), e.status


@api_bp.route("/recommend", methods=["GET", "POST"])
@api_auth
def api_recommend():
    data = (request.get_json(silent=True) or {}) if request.method == "POST" else request.args
    donor_id = _int_arg(data, "donor_id")
    if donor_id is None:
        raise RecommendationError("donor_id is required.")
    result = recommend(
        donor_id=donor_id,
        item_category=data.get("item_category"),
        donor_area=data.get("donor_area") or None,
        limit=_int_arg(data, "limit", default=10, minimum=1, maximum=50),
    )
    return jsonify(result)


@api_bp.route("/stale-listings", methods=["GET"])
@api_auth
def api_stale_listings():
    orphanage_id = _int_arg(request.args, "orphanage_id")
    listings = get_stale_listings(orphanage_id=orphanage_id)
    return jsonify(stale_after_days=STALE_AFTER_DAYS, count=len(listings), listings=listings)


@api_bp.route("/stale-listings/scan", methods=["POST"])
@api_auth
def api_scan_stale_listings():
    return jsonify(scan_stale_listings())
