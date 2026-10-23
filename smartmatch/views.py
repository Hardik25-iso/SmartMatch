"""SmartMatch web pages: the admin dashboard and the NGO 'still needed' action."""
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from . import ctx
from .alerts import alerts_dry_run, recent_alerts, send_stale_alerts
from .catalog import CATEGORIES
from .features import FEATURES
from .freshness import freshness_summary, get_stale_listings, refresh_listing, scan_stale_listings
from .geo import AREA_NAMES, normalize_area
from .recommender import RecommendationError, model_info, recommend, train_model

smartmatch_bp = Blueprint("smartmatch", __name__, url_prefix="/smartmatch")


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


@smartmatch_bp.route("/dashboard")
@admin_required
def dashboard():
    m = ctx.models
    donors = m.Account.query.filter_by(role="donor").order_by(m.Account.name).all()

    donor_id = request.args.get("donor_id", type=int) or (donors[0].id if donors else None)
    category = request.args.get("item_category") or CATEGORIES[0]
    area = request.args.get("donor_area") or None
    recommendation, rec_error = None, None
    if donor_id:
        try:
            recommendation = recommend(donor_id, category, donor_area=area, limit=10)
        except RecommendationError as e:
            rec_error = str(e)

    week_ago = datetime.utcnow() - timedelta(days=7)
    alerts_last_week = ctx.db.session.query(func.count(m.AlertLog.id)).filter(
        m.AlertLog.created_at >= week_ago).scalar()
    ngo_names = dict(ctx.db.session.query(m.Orphanage.id, m.Orphanage.name).all())

    return render_template(
        "smartmatch/dashboard.html",
        donors=donors, categories=CATEGORIES, areas=AREA_NAMES, feature_names=FEATURES,
        selected={"donor_id": donor_id, "item_category": category, "donor_area": area},
        recommendation=recommendation, rec_error=rec_error,
        freshness=freshness_summary(), stale_listings=get_stale_listings(),
        alerts=recent_alerts(15), alerts_last_week=alerts_last_week, ngo_names=ngo_names,
        model=model_info(), dry_run=alerts_dry_run(),
    )


@smartmatch_bp.route("/scan", methods=["POST"])
@admin_required
def run_scan():
    result = scan_stale_listings()
    flash(f"Freshness scan done: {result['newly_stale']} newly stale, {result['still_stale']} still stale, "
          f"{result['recovered']} recovered.", "success")
    return redirect(url_for("smartmatch.dashboard", **_keep_query()))


@smartmatch_bp.route("/alerts", methods=["POST"])
@admin_required
def run_alerts():
    s = send_stale_alerts()
    mode = " (dry run: nothing was actually sent)" if s["dry_run_mode"] else ""
    flash(f"Alerts{mode}: {s['sent']} sent, {s['dry_run']} logged, {s['failed']} failed, "
          f"{s['skipped_cooldown']} NGOs skipped (alerted recently).", "danger" if s["failed"] else "success")
    return redirect(url_for("smartmatch.dashboard", **_keep_query()))


@smartmatch_bp.route("/train", methods=["POST"])
@admin_required
def run_training():
    try:
        bundle = train_model(log=ctx.app.logger.info)
        learned = bundle["metrics"]["learned"]
        flash(f"Model retrained: hit-rate@5 {learned['hit_rate@5']:.0%}, MRR {learned['mrr']:.2f} on "
              f"{bundle['metrics']['holdout_events']} held-out donations.", "success")
    except RecommendationError as e:
        flash(f"Training failed: {e}", "danger")
    return redirect(url_for("smartmatch.dashboard", **_keep_query()))


@smartmatch_bp.route("/for-you")
@login_required
def for_you():
    """Donor-facing recommendations: the ranked NGOs for what this donor wants to give."""
    if current_user.role != "donor":
        abort(403)
    category = request.args.get("item_category") or CATEGORIES[0]
    area = request.args.get("donor_area") or None
    recommendation, error = None, None
    try:
        recommendation = recommend(current_user.id, category, donor_area=area, limit=6)
    except RecommendationError as e:
        error = str(e)
    return render_template(
        "smartmatch/for_you.html",
        categories=CATEGORIES, areas=AREA_NAMES,
        selected={"item_category": category, "donor_area": area or normalize_area(current_user.area)},
        recommendation=recommendation, error=error,
    )


@smartmatch_bp.route("/listings/<int:item_id>/refresh", methods=["POST"])
@login_required
def refresh_need(item_id):
    """The owning NGO confirms a listing is still needed."""
    if current_user.role != "ngo":
        abort(403)
    item = ctx.models.ItemNeeded.query.get_or_404(item_id)
    if item.orphanage.account != current_user:
        abort(403)
    refresh_listing(item)
    ctx.db.session.commit()
    flash(f"Thanks! '{item.item_name}' is confirmed as still needed.", "success")
    return redirect(url_for("dashboard"))


def _keep_query():
    return {k: v for k, v in request.form.items() if k in ("donor_id", "item_category", "donor_area") and v}
