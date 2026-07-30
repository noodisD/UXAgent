"""ISO 9241-11 signal separation for a persona run.

The methodology base (§1.1) requires that a simulation produce *separate*
signals for effectiveness (did the task succeed), efficiency (resources spent),
and satisfaction (synthetic self-report, always labelled as such) — not one
blended "persona opinion". This module derives those three buckets from the
trace so the report can present them independently, each tied to its context.

Where each signal actually comes from — read this before trusting one:

  efficiency     the interaction log. step_count, backtracks, fallbacks and
                 frustration are counted from the trace. Measured.
  effectiveness  OBSERVED from the trace (§4.2) where the task's target rung is
                 machine-decidable: session_goal_state climbs a same-site goal
                 ladder and compares the highest rung reached against the target
                 the intent asks for. `outcome_source: "observed"` marks these.
                 When the target is undecidable (non-shopping / oblique intent)
                 or there is no on-site evidence, it falls back to the LLM's
                 `objective_reflection.outcome` (`outcome_source: "declared"`),
                 and any observed/declared conflict is flagged (`disagreement`).
                 `ended_reason`/`critical_error` come from the harness.
  satisfaction   a synthetic self-report, labelled as such in the output.

So effectiveness is now evidence wherever the task's success condition can be
checked; only the abstain path still rests on the LLM, and it says so.
"""
from __future__ import annotations

from .attribution import _parse_action, harness_suspect_steps
from .session_goal_state import observed_outcome
from .site_scope import target_url_for
from .trace_store import TraceStore

# Declared outcomes the LLM may emit — used only to detect observed/declared
# disagreement.
_VALID_DECLARED = {"success", "partial", "failed"}

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

    # --- effectiveness — OBSERVED from the trace where the task's target rung is
    # machine-decidable (§4.2, session_goal_state). The LLM's declared outcome is
    # kept as a labelled fallback (undecidable target / no on-site evidence) and
    # any observed/declared disagreement is flagged. Only ended_reason/
    # critical_error are harness-observed. ---
    declared = (reflection.get("outcome") or "").lower()
    observed = observed_outcome(store, target_url_for(store))
    if observed["decidable"]:
        task_success, goal_state_reached = _OUTCOME_MAP[observed["outcome"]]
        outcome_source = "observed"
    else:
        task_success, goal_state_reached = _OUTCOME_MAP.get(declared, ("unknown", False))
        outcome_source = "declared"
    disagreement = (
        observed["decidable"]
        and declared in _VALID_DECLARED
        and observed["outcome"] != declared
    )
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
            # Provenance of task_success/goal_state_reached: "observed" (trace
            # evidence) or "declared" (LLM fallback). See session_goal_state.
            "outcome_source": outcome_source,
            "declared_outcome": declared or None,
            "observed_outcome": observed["outcome"],
            "target_rung": observed["target_rung"],
            "target_source": observed["target_source"],
            "reached_rung": observed["reached_rung"],
            "disagreement": bool(disagreement),
            "evidence": observed["evidence"],
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
