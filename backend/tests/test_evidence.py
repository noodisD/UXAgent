import json
from pathlib import Path

import pytest

from simulated_web_agent.main.evidence import resolve_all, resolve_evidence
from simulated_web_agent.main.trace_store import TraceStore


@pytest.fixture()
def trace(tmp_path: Path) -> TraceStore:
    store = TraceStore(tmp_path / "persona")
    # two observation steps with URLs
    store.append_observation({"tabs": [{"url": "https://example.com/", "is_active": True}]})
    store.append_observation({"tabs": [{"url": "https://example.com/cart", "is_active": True}]})
    store.write_observation_step_text(0, "I see the homepage with a search bar.")
    store.write_observation_step_text(1, "I see the cart page. The remove button is missing a label.")
    # screenshot exists only for step 1
    store.screenshot_path(1).write_bytes(b"\x89PNG fake")
    return store


def test_valid_step_resolves_full_evidence(trace):
    finding = {"step": 1, "url": "https://wrong.example/claimed", "evidence": "x"}
    out = resolve_evidence(trace, finding)
    assert out["verified"] is True
    assert out["url"] == "https://example.com/cart"
    assert out["claimed_url"] == "https://wrong.example/claimed"
    assert out["screenshot"] == "screenshot/screenshot_1.png"
    assert "remove button" in out["observation_quote"]


def test_out_of_range_step_is_unverified(trace):
    out = resolve_evidence(trace, {"step": 99, "url": "https://example.com/"})
    assert out["verified"] is False
    assert out["screenshot"] is None


def test_missing_step_is_unverified(trace):
    out = resolve_evidence(trace, {"url": "https://example.com/"})
    assert out["verified"] is False


def test_step_without_screenshot_still_verifies_via_quote(trace):
    out = resolve_evidence(trace, {"step": 0})
    assert out["verified"] is True
    assert out["screenshot"] is None
    assert "homepage" in out["observation_quote"]


def test_resolve_all_tolerates_garbage(trace):
    out = resolve_all(trace, [{"step": 1}, "not-a-dict", {"step": None}])
    assert out[0]["verified"] is True
    assert out[1] == "not-a-dict"
    assert out[2]["verified"] is False
