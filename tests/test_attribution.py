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
    store.append_observation({"tabs": [{"url": "https://pepco.pl/search?searchString=poduszki"}]})
    findings = [{"heuristic_id": 2, "severity": "poważny", "step": 0,
                 "evidence": "Wyszukiwarka nie przetwarza zapytań i nie zwraca wyników."}]
    flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is True


def test_search_broken_claim_not_flagged_when_search_never_ran(tmp_path):
    # no /search URL in the trace → nothing to contradict; leave it for a human.
    store = _store(tmp_path, [{"action": "type", "target": "wyszukaj_produkt",
                               "description": "search"}])
    store.append_observation({"tabs": [{"url": "https://pepco.pl/"}]})
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
