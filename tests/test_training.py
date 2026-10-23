from datetime import datetime

from app import Account, ItemNeeded
from smartmatch import recommender
from smartmatch.synthetic import generate


def test_synthetic_data_then_training_then_recommend(app, monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTMATCH_MODEL_PATH", str(tmp_path / "model.joblib"))
    summary = generate(n_donors=40, n_ngos=12, n_donations=500, seed=7, log=lambda *_: None)
    assert summary["donations"] > 300
    assert summary["older_than_30_days"] > 0

    now = datetime.utcnow()
    assert ItemNeeded.query.filter(ItemNeeded.date_posted > now).count() == 0
    gifts = ItemNeeded.query.filter(ItemNeeded.donor_id.isnot(None)).all()
    assert all(g.status in ("Pledged", "Fulfilled") and g.pledge_timestamp >= g.date_posted for g in gifts)

    bundle = recommender.train_model(holdout_frac=0.2, log=lambda *_: None)
    metrics = bundle["metrics"]
    assert metrics["holdout_events"] > 0
    assert 0 < metrics["learned"]["hit_rate@5"] <= 1
    # Donors in the simulation prefer nearby NGOs, so proximity must be learned as a positive signal.
    assert bundle["coefficients"]["proximity"] > 0

    donor = Account.query.filter_by(role="donor").first()
    result = recommender.recommend(donor.id, "Food")
    assert result["model"]["type"] == "pairwise_logistic_regression"
    assert result["results"]
