"""Severity is what the participant lost, not how long the path felt.

The same finding on the same trace (Marta, step 4, "the mortgage page has no link
to the simulator") was "drobny" in one audit and "poważny" in the next, with no
new evidence: she used the site search, reached the simulator and completed the
simulation, with no failed action. The serious label cost 0.2 of the score.
"""
import json

from simulated_web_agent.main import consequence
from simulated_web_agent.main.consequence import (
    apply_severity_ceiling, consequence_record, corroborated_findings, severity_ceiling,
)
from simulated_web_agent.main.prioritization import prioritize
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.trace_store import TraceStore

PAGE = "https://x.pl/kredyt"
FAIL = "Error executing action: Locator.click: Timeout 10000ms exceeded"


def _store(tmp_path, name="p1", outcome="success", n=10, fail_at=(), target="symulator",
           ended="terminated"):
    """n steps on PAGE; observation k+1 carries the error of action k."""
    store = TraceStore(tmp_path / name)
    store.write_action_trace([json.dumps({"action": "click", "target": target if k in fail_at else f"t{k}",
                                          "description": f"Click {k}"}) for k in range(n)])
    for k in range(n):
        err = FAIL if (k - 1) in fail_at else None
        store.append_observation({"tabs": [{"url": PAGE, "is_active": True}], "error": err})
    store.write_session_result({"steps_taken": n, "max_steps": 40, "ended_reason": ended})
    store.write_objective_reflection({"outcome": outcome})
    return store


def _finding(severity="poważny", step=4, **kw):
    return {"heuristic_id": 7, "severity": severity, "step": step, "url": PAGE,
            "evidence": "Brakuje linku do symulatora rat.", "verified": True,
            "claim_verdict": "supported", **kw}


def test_marta_shape_serious_is_capped_to_minor(tmp_path):
    [f] = apply_severity_ceiling(_store(tmp_path), [_finding()])
    assert f["severity"] == "drobny"
    assert f["severity_before_cap"] == "poważny"
    assert f["severity_capped_by"] == "task_completed_no_failure"
    assert f["severity_note"]
    assert f["consequence"]["blocked"] is False and f["consequence"]["failed_actions"] == 0


def test_two_failed_actions_on_the_control_keep_the_label(tmp_path):
    store = _store(tmp_path, fail_at=(5, 6))
    [f] = apply_severity_ceiling(store, [_finding()])
    assert f["severity"] == "poważny"
    assert "severity_before_cap" not in f
    assert f["consequence"]["failed_actions"] == 2


def test_two_failures_on_different_controls_of_the_page_keep_serious_not_critical(tmp_path):
    store = _store(tmp_path, fail_at=(5, 6))
    actions = [json.loads(a) for a in store.read_action_trace()]
    actions[6]["target"] = "inny"
    store.write_action_trace([json.dumps(a) for a in actions])
    record = consequence_record(store, _finding())
    assert record["failed_actions"] == 2 and record["blocked"] is False
    assert apply_severity_ceiling(store, [_finding()])[0]["severity"] == "poważny"
    assert apply_severity_ceiling(store, [_finding("krytyczny")])[0]["severity"] == "poważny"


def test_one_failed_action_is_not_enough(tmp_path):
    [f] = apply_severity_ceiling(_store(tmp_path, fail_at=(5,)), [_finding()])
    assert f["severity"] == "drobny"


def test_a_second_independent_participant_allows_serious(tmp_path):
    a, b = _store(tmp_path, "p1"), _store(tmp_path, "p2")
    fa, fb = _finding(_persona_dir="p1"), _finding(_persona_dir="p2")
    both = [fa, fb]
    corro = corroborated_findings(both)
    assert corro == {0, 1}
    apply_severity_ceiling(a, [fa], corro, offset=0)
    apply_severity_ceiling(b, [fb], corro, offset=1)
    assert fa["severity"] == fb["severity"] == "poważny"
    # a different heuristic or page is not the same issue
    other = _finding(_persona_dir="p2", heuristic_id=3)
    assert corroborated_findings([fa, other]) == set()


def test_blocked_or_failed_session_allows_critical(tmp_path):
    [f] = apply_severity_ceiling(_store(tmp_path, outcome="failed"), [_finding("krytyczny")])
    assert f["severity"] == "krytyczny"
    abandoned = _store(tmp_path, "p2", ended="persona_abandoned")
    [g] = apply_severity_ceiling(abandoned, [_finding("krytyczny")])
    assert g["severity"] == "krytyczny" and g["consequence"]["blocked"]


def test_same_control_failing_twice_is_blocked(tmp_path):
    [f] = apply_severity_ceiling(_store(tmp_path, fail_at=(5, 6)), [_finding("krytyczny")])
    assert f["severity"] == "krytyczny"


def test_finding_in_the_last_steps_of_a_session_that_did_not_succeed_is_blocked(tmp_path):
    store = _store(tmp_path, outcome="partial", n=10)
    assert consequence_record(store, _finding(step=8))["blocked"] is True
    assert consequence_record(store, _finding(step=4))["blocked"] is False
    done = _store(tmp_path, "p2", outcome="success")
    assert consequence_record(done, _finding(step=8))["blocked"] is False


def test_critical_without_blocking_is_capped(tmp_path):
    [f] = apply_severity_ceiling(_store(tmp_path), [_finding("krytyczny")])
    assert f["severity"] == "drobny" and f["severity_before_cap"] == "krytyczny"


def test_unknown_consequence_never_lowers(tmp_path):
    store = _store(tmp_path)
    for bad in (_finding(step=None), _finding(step=99), {"heuristic_id": 7, "severity": "poważny"}):
        f = dict(bad)
        apply_severity_ceiling(store, [f])
        assert f["severity"] == "poważny"
    assert apply_severity_ceiling(None, [_finding()])[0]["severity"] == "poważny"
    empty = TraceStore(tmp_path / "empty")
    assert apply_severity_ceiling(empty, [_finding()])[0]["severity"] == "poważny"
    unknown = _store(tmp_path, "p3", outcome="unknown")
    assert apply_severity_ceiling(unknown, [_finding()])[0]["severity"] == "poważny"
    assert severity_ceiling(None) is None


def test_idempotent_and_corroboration_restores_the_label(tmp_path):
    store = _store(tmp_path)
    f = _finding()
    apply_severity_ceiling(store, [f])
    once = json.dumps(f, sort_keys=True)
    apply_severity_ceiling(store, [f])
    assert json.dumps(f, sort_keys=True) == once
    # recomputed from the label before the cap, so a later corroboration lifts it
    apply_severity_ceiling(store, [f], {0})
    assert f["severity"] == "poważny" and "severity_before_cap" not in f


def test_the_score_deduction_follows_the_capped_label(tmp_path):
    store = _store(tmp_path)
    raw = _finding(_persona_dir="p1")
    capped = apply_severity_ceiling(store, [dict(raw)])[0]

    def deduction(finding):
        rows = prioritize([finding], {"p1": "success"})
        stats = {"prioritization": [{**r, "participant_count": 6} for r in rows],
                 "outcomes": {"participant_count": 6}}
        lines = [d for d in compute_ux_score(stats)["deductions"] if d["kind"] == "issue"]
        return lines[0]["amount"]

    assert deduction(capped) < deduction(raw)
    assert deduction(raw) / deduction(capped) > 2


def test_narrative_issue_is_clamped_to_its_supporting_findings():
    from simulated_web_agent.main.reporter import _clamp_issue_severity, _rebucket_by_severity

    issue = {"severity": "poważny", "heuristic_id": 7}
    _clamp_issue_severity(issue, [{"severity": "drobny"}])
    assert issue["severity"] == "drobny" and issue["severity_before_cap"] == "poważny"
    kept = {"severity": "poważny"}
    _clamp_issue_severity(kept, [{"severity": "drobny"}, {"severity": "poważny"}])
    assert kept["severity"] == "poważny"
    legacy = {"severity": "poważny"}
    _clamp_issue_severity(legacy, [{"severity": ""}])
    assert legacy["severity"] == "poważny"
    narr = {"serious_issues": [issue], "minor_issues": []}
    _rebucket_by_severity(narr)
    assert narr["serious_issues"] == [] and narr["minor_issues"] == [issue]
