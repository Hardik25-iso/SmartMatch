import os
import sys
from datetime import datetime, timedelta

import pytest

# Must be set before app.py is imported: the DB URI is read at import time.
os.environ["DATABASE_URL"] = "sqlite://"
# app.py refuses to start without a real SECRET_KEY.
os.environ.setdefault("SECRET_KEY", "test-only-secret-key")
os.environ["SMARTMATCH_MODEL_PATH"] = os.path.join(os.path.dirname(__file__), "_no_model.joblib")
os.environ.pop("SMARTMATCH_API_KEY", None)
os.environ["ALERTS_DRY_RUN"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as smartmatch  # noqa: E402


@pytest.fixture
def app():
    # CSRF is on in the real app; the test client has no token to send.
    smartmatch.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    # Rate-limit counters are global and in-memory, so without this the 5-per-minute
    # login limit throttles tests that log in late in the run. The limiter reads its
    # config at init time, so set the flag on the object rather than in config.
    smartmatch.limiter.enabled = False
    with smartmatch.app.app_context():
        smartmatch.db.create_all()
        yield smartmatch.app
        smartmatch.db.session.remove()
        smartmatch.db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    return smartmatch.db


@pytest.fixture
def factory(db):
    return Factory(db)


class Factory:
    def __init__(self, db):
        self.db = db
        self.now = datetime.utcnow()
        self._n = 0

    def _next(self):
        self._n += 1
        return self._n

    def account(self, role="donor", area=None, password="11"):
        n = self._next()
        acc = smartmatch.Account(email=f"{role}{n}@test.local", name=f"{role.title()} {n}", role=role, area=area,
                               password=smartmatch.bcrypt.generate_password_hash(password).decode("utf-8"))
        self.db.session.add(acc)
        self.db.session.commit()
        return acc

    def ngo(self, name=None, area="Kothrud", phone="+919000000001"):
        n = self._next()
        acc = self.account(role="ngo")
        org = smartmatch.Orphanage(name=name or f"NGO {n}", city=area, address=f"{area}, Pune",
                                 contact_email=f"ngo{n}@test.local", contact_phone=phone, account=acc)
        self.db.session.add(org)
        self.db.session.commit()
        return org

    def need(self, ngo, category="Food", item_name="Rice Bags (10kg)", age_days=0, status="Active",
             donor=None, pledged_days_ago=None, is_urgent=False, quantity=50):
        stamp = self.now - timedelta(days=age_days)
        item = smartmatch.ItemNeeded(
            item_name=item_name, category=category, quantity_needed=quantity, is_urgent=is_urgent,
            status=status, orphanage_id=ngo.id, date_posted=stamp, last_updated=stamp,
            donor_id=donor.id if donor else None,
            pledge_timestamp=self.now - timedelta(days=pledged_days_ago) if pledged_days_ago is not None else None,
        )
        self.db.session.add(item)
        self.db.session.commit()
        return item


def login(client, account, password="11"):
    return client.post("/login", data={"email": account.email, "password": password})
