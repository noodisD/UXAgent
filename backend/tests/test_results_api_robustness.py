"""A malformed artifact from one persona must not take down the whole report.

Observed in run 2026-07-16_13-41-14: Gemini returned a bare array of weakness
strings for first_impression, so first_impression.json held a list. build_persona
did fi.get("scores") -> AttributeError -> GET /runs/<id> 500 -> no report at all.
"""
import json

from simulated_web_agent.main.results_api import _effective_outcome, build_persona
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


# --- the persona payload's outcome must be the SHARED definition ------------
#
# _effective_outcome exists to be "one definition for the persona payload, the
# history counts and the run comparison", but build_persona used to re-derive it
# inline without the empty-observed_outcome fallback — so a persona's detail card
# could report a different outcome than its own row in the run history.

def _outcome_store(tmp_path, name, effectiveness, declared="partial"):
    store = TraceStore(tmp_path / name)
    store.write_basic_info({"persona": f"Persona: {name}", "intent": "y"})
    store.write_objective_reflection({"outcome": declared})
    store.write_iso_metrics({"effectiveness": effectiveness})
    return store


def test_observed_outcome_is_used_when_present(tmp_path):
    store = _outcome_store(
        tmp_path, "obs",
        {"outcome_source": "observed", "observed_outcome": "success"},
        declared="failed")
    out = build_persona(store.trace_dir, {})
    assert out["outcome"] == "success"
    assert out["declaredOutcome"] == "failed"


def test_empty_observed_outcome_falls_back_to_declared(tmp_path):
    """The regression this closes: source says "observed" but the value is
    empty — the payload must fall back to declared, not emit None."""
    store = _outcome_store(
        tmp_path, "empty",
        {"outcome_source": "observed", "observed_outcome": ""},
        declared="partial")
    out = build_persona(store.trace_dir, {})
    assert out["outcome"] == "partial"


def test_payload_outcome_matches_the_shared_definition(tmp_path):
    """Pin the two together so they cannot drift apart again."""
    for i, (eff, declared) in enumerate([
        ({"outcome_source": "observed", "observed_outcome": "partial"}, "failed"),
        ({"outcome_source": "observed", "observed_outcome": ""}, "success"),
        ({"outcome_source": "declared"}, "failed"),
        ({}, "success"),
    ]):
        store = _outcome_store(tmp_path, f"pin{i}", eff, declared=declared)
        out = build_persona(store.trace_dir, {})
        assert out["outcome"] == _effective_outcome(store), f"case {i}"


def test_a_held_finding_carries_the_sentence_that_held_it(tmp_path):
    """The chip names the kind of hold; the tooltip says which signal held this one."""
    store = TraceStore(tmp_path / "p3")
    store.write_basic_info({"persona": "Persona: Z", "intent": "y"})
    (store.trace_dir / "heuristic_findings.json").write_text(json.dumps([
        {"heuristic_id": 1, "severity": "drobny", "harness_suspect": True,
         "harness_cause": {"code": "consent", "detail": "", "step": 4}},
        {"heuristic_id": 2, "severity": "drobny", "verified": True},
    ]), encoding="utf-8")
    held, confirmed = build_persona(store.trace_dir, {})["heuristics"]
    assert "baner zgody" in held["holdReason"]
    assert confirmed["holdReason"] == ""
