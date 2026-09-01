"""Cross-origin access for the separately-hosted dashboard.

What is worth testing here is our configuration, not flask-cors itself: that the
allowlist is respected, and that X-API-Key is permitted — the frontend sends it
on every call, and if the preflight omits it every request fails with an opaque
CORS error.
"""
from flask import Flask

from simulated_web_agent.main.app import configure_cors

ALLOWED = "https://ux.example.com"
DENIED = "https://evil.example.com"


def _app_with_cors(origins):
    a = Flask(__name__)
    configure_cors(a, origins)

    @a.get("/ping")
    def ping():
        return {"ok": True}

    a.config.update(TESTING=True)
    return a


def test_no_origins_configured_adds_no_cors_headers():
    """Default is same-origin only — nothing is loosened by accident."""
    c = _app_with_cors([]).test_client()
    res = c.get("/ping", headers={"Origin": ALLOWED})
    assert "Access-Control-Allow-Origin" not in res.headers


def test_allowed_origin_is_echoed():
    c = _app_with_cors([ALLOWED]).test_client()
    res = c.get("/ping", headers={"Origin": ALLOWED})
    assert res.headers.get("Access-Control-Allow-Origin") == ALLOWED


def test_disallowed_origin_gets_no_header():
    c = _app_with_cors([ALLOWED]).test_client()
    res = c.get("/ping", headers={"Origin": DENIED})
    assert res.headers.get("Access-Control-Allow-Origin") != DENIED


def test_preflight_permits_the_api_key_header():
    """The frontend sends X-API-Key on every request; if the preflight does not
    allow it, nothing works cross-origin."""
    c = _app_with_cors([ALLOWED]).test_client()
    res = c.options("/ping", headers={
        "Origin": ALLOWED,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "X-API-Key",
    })
    allowed = res.headers.get("Access-Control-Allow-Headers", "").lower()
    assert "x-api-key" in allowed
