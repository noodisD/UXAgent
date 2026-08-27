"""A malformed artifact from one persona must not take down the whole report.

Observed in run 2026-07-16_13-41-14: Gemini returned a bare array of weakness
strings for first_impression, so first_impression.json held a list. build_persona
did fi.get("scores") -> AttributeError -> GET /runs/<id> 500 -> no report at all.
"""
import json

from simulated_web_agent.main.results_api import build_persona
from simulated_web_agent.main.trace_store import TraceStore


def _persona_dir(tmp_path, first_impression):
    store = TraceStore(tmp_path / "p1")
    store.write_basic_info({"persona": "Persona: Julia", "intent": "kup dekoracje"})
    store.write_first_impression(first_impression)
    return store.trace_dir


def test_list_first_impression_degrades_instead_of_crashing(tmp_path):
    # the exact bad shape seen in the wild
    d = _persona_dir(tmp_path, ["Przycisk CTA słabo kontrastuje z tłem.", "Nawigacja poniżej banera."])
    out = build_persona(d, {})
    assert out["id"] == "p1"
    # scores unavailable, but the persona still renders
    assert all(v is None for v in out["firstImpression"].values())


def test_well_formed_first_impression_still_works(tmp_path):
    d = _persona_dir(tmp_path, {"overall_score": 3.4, "scores": {"purpose_clarity": 4}})
    out = build_persona(d, {})
    assert out["firstImpression"]["purpose"] == 4


def test_garbage_heuristics_are_skipped(tmp_path):
    store = TraceStore(tmp_path / "p2")
    store.write_basic_info({"persona": "Persona: X", "intent": "y"})
    # a stray string among the findings must not break the report
    (store.trace_dir / "heuristic_findings.json").write_text(
        json.dumps(["oops", {"heuristic_id": 1, "severity": "drobny"}]), encoding="utf-8"
    )
    out = build_persona(store.trace_dir, {})
    assert len(out["heuristics"]) == 1
