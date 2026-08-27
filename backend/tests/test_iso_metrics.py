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


# --- §4.2: effectiveness now OBSERVED where the target is decidable ---------

_DM = "https://www.dm.pl"
_SHOP = "dodaj do koszyka i przejdź do podsumowania zamówienia"


def _store_obs(tmp_path, steps, *, intent, outcome, session=None):
    s = TraceStore(tmp_path / "p")
    s.write_action_trace([json.dumps({"action": "click", "description": "c"})] * len(steps))
    for i, (url, obs) in enumerate(steps):
        s.append_observation({"tabs": [{"url": url, "is_active": True}]})
        s.write_observation_step_text(i, obs)
    s.write_basic_info({"intent": intent})
    s.write_objective_reflection({"outcome": outcome})
    s.write_session_result(session or {"steps_taken": len(steps), "ended_reason": "terminated"})
    return s


def test_observed_outcome_overrides_declared_and_flags_disagreement(tmp_path):
    # Trace reaches the order summary; the LLM declared "failed". Observed wins.
    s = _store_obs(tmp_path,
                   [(f"{_DM}/koszyk", "Podsumowanie. Produkty łącznie 20 zł. Przejdź do kasy.")],
                   intent=_SHOP, outcome="failed")
    eff = compute_iso_metrics(s)["effectiveness"]
    assert eff["task_success"] == "success"
    assert eff["goal_state_reached"] is True
    assert eff["outcome_source"] == "observed"
    assert eff["declared_outcome"] == "failed"
    assert eff["disagreement"] is True
    assert eff["target_rung"] == "order_summary_reached"


def test_declared_fallback_when_target_undecidable(tmp_path):
    # Non-shopping intent → checker abstains → declared kept, no disagreement.
    s = _store_obs(tmp_path, [(f"{_DM}/artykul", "artykuł o zdrowiu")],
                   intent="Przeczytaj artykuł", outcome="success")
    eff = compute_iso_metrics(s)["effectiveness"]
    assert eff["outcome_source"] == "declared"
    assert eff["task_success"] == "success"
    assert eff["observed_outcome"] == "unknown"
    assert eff["disagreement"] is False


def test_observed_agrees_with_declared_no_disagreement(tmp_path):
    # Item added to cart (partial floor) but no summary → observed partial.
    s = _store_obs(tmp_path, [
        (f"{_DM}/p/d/1/a", "produkt"),
        (f"{_DM}/koszyk", "Twój koszyk. Produkty łącznie 20,00 zł. Usuń."),
    ], intent=_SHOP, outcome="partial")
    eff = compute_iso_metrics(s)["effectiveness"]
    assert eff["outcome_source"] == "observed"
    assert eff["observed_outcome"] == "partial"
    assert eff["disagreement"] is False
