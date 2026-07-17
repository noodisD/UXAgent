"""ISO 9241-11 signal separation for a persona run.

The methodology base (§1.1) requires that a simulation produce *separate*
signals for effectiveness (did the task succeed), efficiency (resources spent),
and satisfaction (synthetic self-report, always labelled as such) — not one
blended "persona opinion". This module derives those three buckets from the
trace so the report can present them independently, each tied to its context.

Where each signal actually comes from — read this before trusting one:

  efficiency     the interaction log. step_count, backtracks, fallbacks and
                 frustration are counted from the trace. Measured.
  effectiveness  `objective_reflection.outcome` — an LLM's verdict on the
                 session. NOT measured. `goal_state_reached` is that verdict
                 mapped to a bool, not a checked goal state. Only `ended_reason`
                 and `critical_error` come from the harness.
  satisfaction   a synthetic self-report, labelled as such in the output.

So two of the three buckets rest on an LLM judging its own run. That is a known
gap, not an oversight: METHODOLOGY_PLAN §4.2 would give each task a
machine-checkable `success_condition` (URL pattern, element present) that the
harness verifies at end-of-run, making effectiveness evidence.

Until then, do not present effectiveness as an observed outcome. Checked against
the 53 stored sessions, the labels agree with the traces (no "success" without a
product or cart; no "failed" that reached checkout) — but n=2 for success, and
most of that history predates the harness fixes, so it is weak evidence rather
than validation.
"""
from __future__ import annotations

from .attribution import _parse_action, harness_suspect_steps
from .trace_store import TraceStore

# The LLM's self-reported outcome -> normalized verdict. `goal_state_reached` is
# this declaration cast to a bool; nothing checks that the goal state was reached
# (§4.2). Do not read it as evidence.
_OUTCOME_MAP = {
    "success": ("success", True),
    "partial": ("partial_success", False),
    "failed": ("failed", False),
}


# The fixed post-task questions use a 1–7 scale; keep them out of the SUS/Likert
# (1–5) average.
_POST_TASK_IDS = {"synthetic_seq", "synthetic_confidence"}


def _numeric_survey_avg(survey: dict) -> float | None:
    vals: list[float] = []
    for a in (survey or {}).get("answers", []) or []:
        if a.get("question_id") in _POST_TASK_IDS:
            continue
        v = a.get("value")
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 1 <= f <= 5:  # SUS/Likert scale answers only
            vals.append(f)
    return round(sum(vals) / len(vals), 2) if vals else None


def compute_iso_metrics(store: TraceStore) -> dict:
    """Return effectiveness / efficiency / satisfaction signals for one run."""
    session = store.read_session_result() or {}
    reflection = store.read_objective_reflection() or {}
    survey = store.read_survey_result() or {}
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    suspect = harness_suspect_steps(store)

    # --- effectiveness (an LLM's verdict on its own run — see module docstring;
    # only ended_reason/critical_error below are harness-observed) ---
    outcome = (reflection.get("outcome") or "").lower()
    task_success, goal_state_reached = _OUTCOME_MAP.get(outcome, ("unknown", False))
    ended_reason = session.get("ended_reason")
    critical_error = ended_reason == "error"

    # --- efficiency (pure log signals) ---
    step_count = session.get("steps_taken")
    if step_count is None:
        step_count = len(actions)
    backtracks = sum(1 for a in actions if a.get("action") == "back")
    loop_detected = len(suspect) > 0
    reached_step_limit = ended_reason == "max_steps"

    # --- satisfaction (SYNTHETIC self-report — never a real-user metric) ---
    synth_score = _numeric_survey_avg(survey)
    frustration_signals = backtracks + len(suspect)

    def _answer(qid: str) -> float | None:
        for a in (survey or {}).get("answers", []) or []:
            if a.get("question_id") == qid:
                try:
                    return float(a.get("value"))
                except (TypeError, ValueError):
                    return None
        return None

    synthetic_seq = _answer("synthetic_seq")           # 1–7
    synthetic_confidence = _answer("synthetic_confidence")  # 1–7

    return {
        "effectiveness": {
            "task_success": task_success,
            "goal_state_reached": bool(goal_state_reached),
            "critical_error": bool(critical_error),
            "ended_reason": ended_reason,
        },
        "efficiency": {
            "step_count": step_count,
            "backtracks": backtracks,
            "loop_detected": loop_detected,
            "reached_step_limit": bool(reached_step_limit),
            "optimal_path_delta": None,  # requires a declared optimal path per task
        },
        "satisfaction": {
            "source": "synthetic_self_report",
            "synthetic_score_1_5": synth_score,
            "synthetic_seq_1_7": synthetic_seq,
            "synthetic_confidence_1_7": synthetic_confidence,
            "frustration_signals": frustration_signals,
            "requires_real_user_validation": True,
        },
    }


def run_iso_metrics(store: TraceStore) -> dict:
    """Compute and persist ISO 9241-11 metrics for a run (cheap sync pass)."""
    metrics = compute_iso_metrics(store)
    store.write_iso_metrics(metrics)
    return metrics
