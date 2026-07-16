"""ISO 9241-11 signal separation (§1.1): effectiveness / efficiency /
satisfaction must be distinct, log-derived where possible, and satisfaction
must be labelled synthetic."""
import json

from simulated_web_agent.main.iso_metrics import compute_iso_metrics
from simulated_web_agent.main.trace_store import TraceStore


def _store(tmp_path, *, actions=None, session=None, reflection=None, survey=None):
    s = TraceStore(tmp_path / "p")
    if actions is not None:
        s.write_action_trace([json.dumps(a) for a in actions])
    if session is not None:
        s.write_session_result(session)
    if reflection is not None:
        s.write_objective_reflection(reflection)
    if survey is not None:
        s.write_survey_result(survey)
    return s


def test_effectiveness_from_outcome(tmp_path):
    s = _store(tmp_path, reflection={"outcome": "success"},
               session={"steps_taken": 10, "ended_reason": "terminated"})
    m = compute_iso_metrics(s)
    assert m["effectiveness"]["task_success"] == "success"
    assert m["effectiveness"]["goal_state_reached"] is True
    assert m["effectiveness"]["critical_error"] is False


def test_error_session_marks_critical_error(tmp_path):
    s = _store(tmp_path, reflection={"outcome": "failed"},
               session={"steps_taken": 27, "ended_reason": "error"})
    m = compute_iso_metrics(s)
    assert m["effectiveness"]["task_success"] == "failed"
    assert m["effectiveness"]["critical_error"] is True


def test_efficiency_counts_backtracks_and_loops(tmp_path):
    actions = [
        {"action": "type", "target": "loc", "description": "x"},
        {"action": "type", "target": "loc", "description": "x"},
        {"action": "type", "target": "loc", "description": "x"},  # 3x loop
        {"action": "back", "target": None, "description": "go back"},
    ]
    s = _store(tmp_path, actions=actions,
               session={"steps_taken": 4, "ended_reason": "max_steps"})
    m = compute_iso_metrics(s)
    assert m["efficiency"]["step_count"] == 4
    assert m["efficiency"]["backtracks"] == 1
    assert m["efficiency"]["loop_detected"] is True
    assert m["efficiency"]["reached_step_limit"] is True


def test_satisfaction_is_labelled_synthetic(tmp_path):
    survey = {"answers": [{"question_id": "q1", "value": "4"},
                          {"question_id": "q2", "value": "2"},
                          {"question_id": "q5", "value": "free text"}]}
    s = _store(tmp_path, survey=survey, reflection={"outcome": "partial"})
    m = compute_iso_metrics(s)
    assert m["satisfaction"]["source"] == "synthetic_self_report"
    assert m["satisfaction"]["synthetic_score_1_5"] == 3.0  # avg of 4 and 2
    assert m["satisfaction"]["requires_real_user_validation"] is True
