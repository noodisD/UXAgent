"""SSO bearer tokens from csaapps.pl as an ADDITIONAL way into the API.

Token = base64url(JSON payload) + "." + base64url(HMAC-SHA256(part1, AUTH_SECRET)),
verified per the administrator's guide. The API-key path must stay exactly as
it was: a request needs a valid SSO token OR a valid X-API-Key.
"""
import base64
import hashlib
import hmac
import json
import logging
import time

import pytest
from flask import Flask, g

from simulated_web_agent.main import app as app_module
from simulated_web_agent.main import sso_auth

SECRET = "test-secret-placeholder"
DOMAIN = "example.test"
KEY = "s3cret"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(payload=None, secret=SECRET, raw_body=None, **over):
    p = {"sub": "ms:abc", "email": f"ala@{DOMAIN}", "name": "Ala", "exp": int(time.time()) + 3600}
    p.update(over)
    body = raw_body if raw_body is not None else _b64(json.dumps(payload if payload is not None else p).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def sso(monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", "")
    monkeypatch.setattr(app_module, "_AUTH_SECRET", SECRET)
    monkeypatch.setattr(app_module, "_ALLOWED_DOMAINS", frozenset({DOMAIN}))


def bearer(tok, scheme="Bearer"):
    return {"Authorization": f"{scheme} {tok}"}


# --- verify_token ----------------------------------------------------------

def test_verify_valid_token_returns_payload():
    p = sso_auth.verify_token(make_token(), SECRET, {DOMAIN})
    assert p["email"] == f"ala@{DOMAIN}" and p["sub"] == "ms:abc"


def test_verify_tampered_signature():
    body, sig = make_token().split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert sso_auth.verify_token(f"{body}.{flipped}", SECRET, {DOMAIN}) is None


def test_verify_tampered_body_keeps_old_signature():
    _, sig = make_token().split(".")
    evil = _b64(json.dumps({"email": f"x@{DOMAIN}", "exp": 9999999999}).encode())
    assert sso_auth.verify_token(f"{evil}.{sig}", SECRET, {DOMAIN}) is None


def test_verify_wrong_secret():
    assert sso_auth.verify_token(make_token(secret="other"), SECRET, {DOMAIN}) is None


def test_verify_expired_and_boundary():
    now = 1_000_000
    assert sso_auth.verify_token(make_token(exp=now - 1), SECRET, {DOMAIN}, now=now) is None
    assert sso_auth.verify_token(make_token(exp=now), SECRET, {DOMAIN}, now=now) is not None


def test_verify_wrong_domain_and_suffix_trick():
    assert sso_auth.verify_token(make_token(email="a@other.test"), SECRET, {DOMAIN}) is None
    assert sso_auth.verify_token(make_token(email=f"a@{DOMAIN}.evil.test"), SECRET, {DOMAIN}) is None
    # the domain is the part after the LAST '@'
    assert sso_auth.verify_token(make_token(email=f"{DOMAIN}@other.test"), SECRET, {DOMAIN}) is None


def test_verify_domain_is_case_insensitive():
    assert sso_auth.verify_token(make_token(email=f"A@{DOMAIN.upper()}"), SECRET, {DOMAIN}) is not None


def test_verify_empty_allowed_domains_rejects_everything():
    assert sso_auth.verify_token(make_token(), SECRET, set()) is None
    assert sso_auth.verify_token(make_token(), SECRET, frozenset()) is None


def test_verify_empty_secret_rejects():
    assert sso_auth.verify_token(make_token(secret=""), "", {DOMAIN}) is None


@pytest.mark.parametrize("tok", [
    "", "nodot", "a.b.c", ".", "!!!.???", None, 123, b"bytes.bytes", "é.é",
])
def test_verify_malformed_never_raises(tok):
    assert sso_auth.verify_token(tok, SECRET, {DOMAIN}) is None


def test_verify_signed_but_not_json_or_not_a_dict():
    assert sso_auth.verify_token(make_token(raw_body=_b64(b"not json")), SECRET, {DOMAIN}) is None
    assert sso_auth.verify_token(make_token(raw_body="@@@@"), SECRET, {DOMAIN}) is None
    assert sso_auth.verify_token(make_token(payload=[1, 2]), SECRET, {DOMAIN}) is None
    assert sso_auth.verify_token(make_token(payload="str"), SECRET, {DOMAIN}) is None


@pytest.mark.parametrize("payload", [
    {"exp": 9999999999},
    {"email": f"a@{DOMAIN}"},
    {"email": None, "exp": 9999999999},
    {"email": 5, "exp": 9999999999},
    {"email": f"a@{DOMAIN}", "exp": "9999999999"},
    {"email": f"a@{DOMAIN}", "exp": True},
    {"email": DOMAIN, "exp": 9999999999},
])
def test_verify_missing_or_bad_fields(payload):
    assert sso_auth.verify_token(make_token(payload=payload), SECRET, {DOMAIN}) is None


def test_verify_uses_compare_digest(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(sso_auth.hmac, "compare_digest", spy)
    sso_auth.verify_token(make_token(), SECRET, {DOMAIN})
    assert calls


def test_parse_domains():
    assert sso_auth.parse_domains(" A.test, b.test ,,") == frozenset({"a.test", "b.test"})
    assert sso_auth.parse_domains("") == frozenset()


# --- Authorization header parsing -----------------------------------------

@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_bearer_scheme_is_case_insensitive(client, sso, scheme):
    assert client.get("/progress", headers=bearer(make_token(), scheme)).status_code == 200


def test_bearer_with_extra_spaces(client, sso):
    headers = {"Authorization": f"Bearer   {make_token()}  "}
    assert client.get("/progress", headers=headers).status_code == 200


def test_other_scheme_is_rejected(client, sso):
    assert client.get("/progress", headers=bearer(make_token(), "Basic")).status_code == 401
    assert client.get("/progress", headers={"Authorization": make_token()}).status_code == 401


# --- routes ---------------------------------------------------------------

def test_valid_token_passes_and_bad_ones_are_401(client, sso):
    assert client.get("/progress", headers=bearer(make_token())).status_code == 200
    for bad in (make_token(secret="x"), make_token(exp=1), make_token(email="a@other.test")):
        res = client.get("/progress", headers=bearer(bad))
        assert res.status_code == 401
        assert res.get_json()["error"] == "unauthorized"
    assert client.get("/progress").status_code == 401


def test_empty_allowed_domains_rejects_valid_signature(client, sso, monkeypatch):
    monkeypatch.setattr(app_module, "_ALLOWED_DOMAINS", frozenset())
    assert client.get("/progress", headers=bearer(make_token())).status_code == 401


def test_health_and_preflight_stay_open(client, sso):
    assert client.get("/health").status_code == 200
    assert client.options("/progress").status_code != 401


# --- the modes ------------------------------------------------------------

def test_key_only_mode_ignores_bearer(client, monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", KEY)
    monkeypatch.setattr(app_module, "_AUTH_SECRET", "")
    assert client.get("/progress", headers={"X-API-Key": KEY}).status_code == 200
    assert client.get("/progress", headers=bearer(make_token())).status_code == 401


def test_sso_only_mode_ignores_api_key(client, sso):
    assert client.get("/progress", headers={"X-API-Key": KEY}).status_code == 401


def test_both_modes_accept_either(client, sso, monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", KEY)
    assert client.get("/progress", headers={"X-API-Key": KEY}).status_code == 200
    assert client.get("/progress", headers=bearer(make_token())).status_code == 200
    assert client.get("/progress", headers={"X-API-Key": "nope", **bearer("junk")}).status_code == 401
    # a wrong key does not poison a valid token
    assert client.get("/progress", headers={"X-API-Key": "nope", **bearer(make_token())}).status_code == 200


def test_neither_mode_is_open(client, monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", "")
    monkeypatch.setattr(app_module, "_AUTH_SECRET", "")
    assert client.get("/progress").status_code == 200


def test_boot_log_names_modes(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=app_module.log.name)
    monkeypatch.setattr(app_module, "_API_KEY", KEY)
    monkeypatch.setattr(app_module, "_AUTH_SECRET", SECRET)
    monkeypatch.setattr(app_module, "_ALLOWED_DOMAINS", frozenset())
    app_module._log_auth_mode()
    text = caplog.text
    assert "SSO bearer token or X-API-Key" in text
    assert "ALLOWED_DOMAINS is empty" in text
    assert SECRET not in text and KEY not in text
    caplog.clear()
    monkeypatch.setattr(app_module, "_API_KEY", "")
    monkeypatch.setattr(app_module, "_AUTH_SECRET", "")
    app_module._log_auth_mode()
    assert "OPEN" in caplog.text


# --- ?token= / ?key= ------------------------------------------------------

ASSET_PATHS = [
    "/runs/RID/report.html",
    "/runs/RID/report.pdf",
    "/runs/RID/report.docx",
    "/runs/RID/screenshot/PID",
    "/runs/RID/screenshot/PID/3",
    "/runs/RID/PID/screenshot/shot.png",
]


@pytest.mark.parametrize("path", ASSET_PATHS)
def test_asset_routes_accept_query_token(client, sso, path):
    # 404/500 = reached the handler; 401 is the assertion under test.
    assert client.get(f"{path}?token={make_token()}").status_code != 401
    assert client.get(f"{path}?token={make_token(secret='x')}").status_code == 401
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ["/progress", "/runs", "/runs/RID", "/runs/RID/live"])
def test_query_token_is_not_a_loophole_for_the_json_api(client, sso, path):
    assert client.get(f"{path}?token={make_token()}").status_code == 401


def test_query_key_unchanged_when_sso_is_also_on(client, sso, monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", KEY)
    assert client.get(f"/runs/RID/report.html?key={KEY}").status_code != 401
    assert client.get("/runs/RID/report.html?key=nope").status_code == 401
    assert client.get(f"/progress?key={KEY}").status_code == 401


def test_query_token_ignored_without_sso(client, monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", KEY)
    monkeypatch.setattr(app_module, "_AUTH_SECRET", "")
    assert client.get(f"/runs/RID/report.html?token={make_token()}").status_code == 401


def test_asset_responses_are_private_and_not_cached(client, sso, tmp_path, monkeypatch):
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")
    monkeypatch.setattr(app_module.results_api, "step_screenshot", lambda *a: shot)
    res = client.get(f"/runs/RID/screenshot/PID/1?token={make_token()}")
    assert res.status_code == 200
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert res.headers["Cache-Control"] == "private, no-store"


def test_existing_no_store_is_kept(client, sso, tmp_path, monkeypatch):
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")
    monkeypatch.setattr(app_module.results_api, "latest_screenshot", lambda *a: shot)
    res = client.get(f"/runs/RID/screenshot/PID?token={make_token()}")
    assert res.headers["Cache-Control"] == "no-store"
    assert res.headers["Referrer-Policy"] == "no-referrer"


def test_non_asset_response_gets_no_referrer_policy(client, sso):
    assert "Referrer-Policy" not in client.get("/progress", headers=bearer(make_token())).headers


# --- identity on g / logging ----------------------------------------------

def test_verified_user_is_logged_on_run(client, sso, monkeypatch, caplog):
    monkeypatch.setattr(app_module, "_run_thread", lambda *a, **k: None)
    body = {
        "total_personas": 1, "demographics": {}, "general_intent": "x",
        "start_url": "https://example.test/", "max_steps": 1, "questionnaire": {},
    }
    caplog.set_level(logging.DEBUG)
    tok = make_token()
    try:
        res = client.post("/run", json=body, headers=bearer(tok))
        assert res.status_code == 202
        assert f"ala@{DOMAIN}" in caplog.text
        assert tok not in caplog.text and SECRET not in caplog.text
    finally:
        app_module.RUNS.clear()
        app_module.CURRENT_RUN_ID = None


def test_g_user_fields(sso):
    seen = {}

    def spy():
        seen["user"] = getattr(g, "user", None)

    with app_module.app.test_request_context("/progress", headers=bearer(make_token())):
        assert app_module._require_api_key() is None
        spy()
    assert seen["user"] == {"email": f"ala@{DOMAIN}", "name": "Ala", "sub": "ms:abc"}


def test_no_log_record_contains_token_or_secret(client, sso, caplog):
    caplog.set_level(logging.DEBUG)
    tok = make_token()
    client.get("/progress", headers=bearer(tok))
    client.get(f"/runs/RID/report.html?token={tok}")
    client.get(f"/runs/RID/report.pdf?token={make_token(secret='x')}")
    client.get("/progress", headers=bearer("garbage.garbage"))
    assert tok not in caplog.text
    assert SECRET not in caplog.text
    assert "garbage.garbage" not in caplog.text


@pytest.mark.parametrize("line", [
    "GET /runs/r/report.html?token=abc.def HTTP/1.1",
    "GET /runs/r/report.html?a=1&token=abc.def&b=2 HTTP/1.1",
    "GET /runs/r/report.html?key=abc HTTP/1.1",
])
def test_werkzeug_access_log_is_scrubbed(line):
    rec = logging.LogRecord(
        "werkzeug", logging.INFO, __file__, 1, '%s - - [d] "%s" %s -',
        ("127.0.0.1", line, "200"), None,
    )
    assert app_module._ScrubQueryCredentials().filter(rec)
    out = rec.getMessage()
    assert "abc" not in out and "REDACTED" in out
    assert ("a=1" in out) == ("a=1" in line) and ("b=2" in out) == ("b=2" in line)


def test_werkzeug_logger_has_the_scrub_filter():
    assert any(isinstance(f, app_module._ScrubQueryCredentials)
               for f in logging.getLogger("werkzeug").filters)


# --- CORS -----------------------------------------------------------------

ORIGIN = "https://csaapps.pl"


def _cors_client():
    a = Flask(__name__)
    app_module.configure_cors(a, [ORIGIN])

    @a.get("/ping")
    def ping():
        return {"ok": True}

    a.config.update(TESTING=True)
    return a.test_client()


def test_preflight_allows_authorization_from_configured_origin():
    res = _cors_client().options("/ping", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization, X-API-Key, Content-Type",
    })
    assert res.headers["Access-Control-Allow-Origin"] == ORIGIN
    allowed = res.headers["Access-Control-Allow-Headers"].lower()
    assert "authorization" in allowed and "x-api-key" in allowed


def test_preflight_rejects_other_origin_and_never_wildcards():
    c = _cors_client()
    res = c.options("/ping", headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    })
    assert "Access-Control-Allow-Origin" not in res.headers
    ok = c.get("/ping", headers={"Origin": ORIGIN})
    assert ok.headers["Access-Control-Allow-Origin"] != "*"


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_exp_is_rejected(bad):
    body = _b64(("{\"sub\": \"ms:1\", \"email\": \"a@%s\", \"name\": \"A\", \"exp\": %s}" % (DOMAIN, bad)).encode())
    assert sso_auth.verify_token(make_token(raw_body=body), SECRET, {DOMAIN}) is None
