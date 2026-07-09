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


def test_normal_session_has_no_suspects(tmp_path):
    actions = [
        {"action": "click", "target": "dom", "description": "open Dom"},
        {"action": "click", "target": "tekstylia", "description": "open textiles"},
        {"action": "type", "target": "wyszukaj_produkt", "description": "search pillows"},
    ]
    assert harness_suspect_steps(_store(tmp_path, actions)) == set()


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
    assert "harness_suspect" not in flagged[1]
