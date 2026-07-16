"""ISO 9241-11 signal separation for a persona run.

The methodology base (§1.1) requires that a simulation produce *separate*
signals for effectiveness (did the task succeed), efficiency (resources spent),
and satisfaction (synthetic self-report, always labelled as such) — not one
blended "persona opinion". This module derives those three buckets from the
trace so the report can present them independently, each tied to its context.

Strongest signal first: effectiveness/efficiency come from the interaction log;
satisfaction is a synthetic self-report and is flagged accordingly.
"""
from __future__ import annotations

from .attribution import _parse_action, harness_suspect_steps
from .trace_store import TraceStore

# outcome (from objective_reflection) -> normalized effectiveness verdict
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

    # --- effectiveness (from the interaction outcome, not a declaration) ---
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
