"""Checks where the security hardening and SmartMatch meet.

CSRF protection is global, so the browser forms must carry a token while the
JSON API (authenticated with X-API-Key) must be exempt, or API clients could
never call it.
"""
import pytest

from tests.conftest import login


@pytest.fixture
def csrf_app(app):
    """The app as it runs in production, with CSRF protection on.

    Tests log in while it is still off, because the login form is protected too,
    then call enable() to switch it on for the request under test.
    """
    def enable():
        app.config.update(WTF_CSRF_ENABLED=True)
    yield app, enable
    app.config.update(WTF_CSRF_ENABLED=False)


def test_api_post_works_without_a_csrf_token(csrf_app, factory, monkeypatch):
    app, enable_csrf = csrf_app
    monkeypatch.setenv("SMARTMATCH_API_KEY", "key")
    client = app.test_client()
    ngo = factory.ngo()
    factory.need(ngo, age_days=45)
    enable_csrf()

    res = client.post("/api/stale-listings/scan", headers={"X-API-Key": "key"})
    assert res.status_code == 200, "the API blueprint must be CSRF-exempt"
    assert res.get_json()["newly_stale"] == 1


def test_browser_forms_still_require_a_csrf_token(csrf_app, factory):
    app, enable_csrf = csrf_app
    client = app.test_client()
    login(client, factory.account(role="admin"))
    enable_csrf()

    assert client.post("/smartmatch/scan").status_code == 400
    page = client.get("/smartmatch/dashboard")
    assert b'name="csrf_token"' in page.data, "the dashboard forms must render a token"


def test_ngo_refresh_form_renders_a_csrf_token(csrf_app, factory):
    from smartmatch.freshness import scan_stale_listings

    app, enable_csrf = csrf_app
    client = app.test_client()
    ngo = factory.ngo()
    factory.need(ngo, age_days=40)
    scan_stale_listings()

    login(client, ngo.account)
    enable_csrf()
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Still needed" in html and 'name="csrf_token"' in html


def test_admin_role_reaches_the_smartmatch_dashboard(client, factory):
    login(client, factory.account(role="admin"))
    assert client.get("/dashboard").headers["Location"].endswith("/smartmatch/dashboard")
