"""The API is going onto the public internet, where every endpoint was open.

A shared key now gates it — but the gate must be invisible when unconfigured,
otherwise local development and this very test suite would need the secret. So
the contract is: no UXAGENT_API_KEY -> behave exactly as before.

The four browser-loaded routes (three screenshot variants + report.html) also
accept the key as ?key=, because a browser cannot put a custom header on an
<img src> or a target="_blank" navigation. Those must NOT be a loophole for the
rest of the API.
"""
import pytest

from simulated_web_agent.main import app as app_module


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", "s3cret")
    return "s3cret"


# --- unconfigured: the gate must not exist ---------------------------------

def test_no_key_configured_leaves_the_api_open(client, monkeypatch):
    """What protects local dev and the other 264 tests."""
    monkeypatch.setattr(app_module, "_API_KEY", "")
    assert client.get("/progress").status_code == 200


# --- configured ------------------------------------------------------------

def test_missing_key_is_rejected(client, with_key):
    res = client.get("/progress")
    assert res.status_code == 401
    assert res.get_json()["error"] == "unauthorized"


def test_wrong_key_is_rejected(client, with_key):
    assert client.get("/progress", headers={"X-API-Key": "nope"}).status_code == 401


def test_correct_key_passes(client, with_key):
    assert client.get("/progress", headers={"X-API-Key": with_key}).status_code == 200


def test_health_is_public(client, with_key):
    """The container platform probes this without a secret."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_preflight_is_not_blocked(client, with_key):
    """A CORS preflight carries no custom header, so gating it would break the
    real request that follows."""
    assert client.options("/progress").status_code != 401


# --- ?key= is scoped to the browser-loaded routes only ---------------------

@pytest.mark.parametrize("path", [
    "/runs/RID/report.html",
    "/runs/RID/screenshot/PID",
    "/runs/RID/screenshot/PID/3",
    "/runs/RID/PID/screenshot/shot.png",
])
def test_asset_routes_accept_query_key(client, with_key, path):
    # 401 is the assertion under test; 404 (run does not exist) means the key
    # was accepted and the request reached the handler.
    assert client.get(f"{path}?key={with_key}").status_code != 401


@pytest.mark.parametrize("path", [
    "/runs/RID/report.html",
    "/runs/RID/screenshot/PID",
])
def test_asset_routes_still_reject_a_wrong_query_key(client, with_key, path):
    assert client.get(f"{path}?key=nope").status_code == 401


def test_query_key_is_not_a_loophole_for_the_json_api(client, with_key):
    """?key= must work only for routes a browser loads directly. If it worked
    everywhere, the key would end up in server logs and Referer headers for the
    whole API."""
    assert client.get(f"/progress?key={with_key}").status_code == 401
    assert client.get(f"/runs?key={with_key}").status_code == 401
