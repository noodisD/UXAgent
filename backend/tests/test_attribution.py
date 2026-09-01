"""Harness-vs-site attribution: findings whose cited step lands on a harness
interaction failure (stuck click/type loop, or the "…nie działał…" fallback)
must be flagged so they aren't shipped as confirmed site defects.

Grounded in run 2026-07-09_11-49-36: the store-locator "type Warszawa 4x, no
change" loop and Kacper's fallback-driven "locator broken" claims.
"""
import json

from simulated_web_agent.main.attribution import (
    flag_harness_suspects,
    harness_suspect_steps,
)
from simulated_web_agent.main.trace_store import TraceStore


def _store(tmp_path, actions):
    store = TraceStore(tmp_path / "persona")
    store.write_action_trace([json.dumps(a) for a in actions])
    return store


def test_empty_observation_artifact_is_suspect(tmp_path):
    """Perception-capture artifact: the observation says the page is (near)
    empty while the step's raw HTML is large — the perceiver missed an overlay
    state, the page was NOT blank. In the diabetyk24 ground-truth run three
    "The page is empty" CRITICAL findings were exactly this (raw_html full,
    agent on an offcanvas overlay). Findings citing such steps must be
    harness-suspect."""
    actions = [
        {"action": "click", "target": "a", "description": "open category"},
        {"action": "click", "target": "b", "description": "open search"},
        {"action": "scroll", "target": None, "description": "scroll down"},
    ]
    store = _store(tmp_path, actions)
    store.write_observation_step_text(1, "The page is empty.")
    store.write_raw_html(1, "<html>" + "x" * 30_000 + "</html>")
    suspect = harness_suspect_steps(store)
    assert 1 in suspect
    assert 0 not in suspect and 2 not in suspect


def test_truly_blank_page_is_not_an_artifact(tmp_path):
    """Tiny observation AND tiny raw HTML = the page really was blank; that is
    a legitimate observation, not a capture failure."""
    actions = [{"action": "click", "target": "x", "description": "open"}] * 2
    store = _store(tmp_path, actions)
    store.write_observation_step_text(1, "The page is empty.")
    store.write_raw_html(1, "<html><body></body></html>")
    assert 1 not in harness_suspect_steps(store)


def test_rich_observation_with_big_html_is_not_an_artifact(tmp_path):
    actions = [{"action": "click", "target": "x", "description": "open"}] * 2
    store = _store(tmp_path, actions)
    store.write_observation_step_text(
        1, "The page shows a product grid with 24 items, filters on the left, "
           "a search bar at the top and pagination at the bottom.")
    store.write_raw_html(1, "<html>" + "x" * 30_000 + "</html>")
    assert 1 not in harness_suspect_steps(store)


def test_fallback_action_marks_step_and_predecessor(tmp_path):
    actions = [
        {"action": "click", "target": "znajdz_sklep", "description": "open locator"},
        {"action": "type", "target": "wyszukaj_lok", "description": "type Kraków"},
        {"action": "back", "target": None,
         "description": "Ten element nie działał mimo kilku prób — wracam i wybieram inną drogę."},
    ]
    suspect = harness_suspect_steps(_store(tmp_path, actions))
    assert 2 in suspect  # the fallback step itself
    assert 1 in suspect  # the failed interaction it retreated from
    assert 0 not in suspect


def test_repeated_target_loop_is_suspect(tmp_path):
    actions = [
        {"action": "type", "target": "wyszukaj_lok", "description": "type Warszawa"},
        {"action": "type", "target": "wyszukaj_lok", "description": "type Warszawa"},
        {"action": "type", "target": "wyszukaj_lok", "description": "type Warszawa"},
    ]
    suspect = harness_suspect_steps(_store(tmp_path, actions))
    assert suspect == {0, 1, 2}


def test_non_consecutive_repeats_are_not_a_loop(tmp_path):
    """Marcin (run 2026-07-16_13-41-14) typed six *different* queries into the
    same search box and Michał revisited one category three times. Normal
    behaviour — a session-wide repeat count wrongly flagged 7 of 9 real
    findings. Only back-to-back retries are a stuck loop."""
    actions = [
        {"action": "type", "target": "wyszukaj_produkt", "description": "search poduszki"},
        {"action": "click", "target": "produkt_a", "description": "open"},
        {"action": "type", "target": "wyszukaj_produkt", "description": "search lampka"},
        {"action": "click", "target": "produkt_b", "description": "open"},
        {"action": "type", "target": "wyszukaj_produkt", "description": "search obrus"},
    ]
    assert harness_suspect_steps(_store(tmp_path, actions)) == set()


def test_consecutive_run_flags_only_that_run(tmp_path):
    actions = [
        {"action": "click", "target": "dom", "description": "open"},
        {"action": "type", "target": "loc", "description": "Warszawa"},
        {"action": "type", "target": "loc", "description": "Warszawa"},
        {"action": "type", "target": "loc", "description": "Warszawa"},
        {"action": "click", "target": "inny", "description": "open"},
    ]
    assert harness_suspect_steps(_store(tmp_path, actions)) == {1, 2, 3}


def test_normal_session_has_no_suspects(tmp_path):
    actions = [
        {"action": "click", "target": "dom", "description": "open Dom"},
        {"action": "click", "target": "tekstylia", "description": "open textiles"},
        {"action": "type", "target": "wyszukaj_produkt", "description": "search pillows"},
    ]
    assert harness_suspect_steps(_store(tmp_path, actions)) == set()


def test_search_broken_claim_flagged_when_search_actually_ran(tmp_path):
    # trace proves the search worked (reached /search) → "search broken" is a
    # contradiction and must be flagged, even with no stuck-loop steps.
    store = _store(tmp_path, [{"action": "type", "target": "wyszukaj_produkt",
                               "description": "search"}])
    store.append_observation({"tabs": [{"url": "https://example-shop.pl/search?searchString=poduszki"}]})
    findings = [{"heuristic_id": 2, "severity": "poważny", "step": 0,
                 "evidence": "Wyszukiwarka nie przetwarza zapytań i nie zwraca wyników."}]
    flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is True


def test_search_broken_claim_not_flagged_when_search_never_ran(tmp_path):
    # no /search URL in the trace → nothing to contradict; leave it for a human.
    store = _store(tmp_path, [{"action": "type", "target": "wyszukaj_produkt",
                               "description": "search"}])
    store.append_observation({"tabs": [{"url": "https://example-shop.pl/"}]})
    findings = [{"heuristic_id": 2, "severity": "poważny", "step": 0,
                 "evidence": "Wyszukiwarka nie przetwarza zapytań."}]
    flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is False


def test_reflagging_clears_a_stale_flag(tmp_path):
    """Flags are persisted, so a finding carrying a flag from an older/looser
    rule must be cleared on re-run — not latched on. Run 2026-07-16_13-41-14
    kept 7 findings marked "needs verification" after the rule was corrected."""
    store = _store(tmp_path, [{"action": "click", "target": "dom", "description": "open"}])
    findings = [{"heuristic_id": 1, "severity": "drobny", "step": 0,
                 "evidence": "real issue",
                 "harness_suspect": True, "requires_human_check": True}]
    flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is False
    assert findings[0]["requires_human_check"] is False


def test_flag_marks_findings_on_suspect_steps(tmp_path):
    actions = [
        {"action": "type", "target": "wyszukaj_lok", "description": "type X"},
        {"action": "type", "target": "wyszukaj_lok", "description": "type X"},
        {"action": "type", "target": "wyszukaj_lok", "description": "type X"},
    ]
    store = _store(tmp_path, actions)
    findings = [
        {"heuristic_id": 1, "severity": "krytyczny", "step": 2, "evidence": "locator broken"},
        {"heuristic_id": 8, "severity": "drobny", "step": 5, "evidence": "real issue"},
    ]
    flagged = flag_harness_suspects(store, findings)
    assert flagged[0]["harness_suspect"] is True
    assert flagged[0]["requires_human_check"] is True
    assert flagged[1]["harness_suspect"] is False
