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

from .attribution import _FALLBACK_MARKERS, harness_suspect_steps
from .trace_store import parse_action as _parse_action
from .session_goal_state import observed_outcome
from .site_scope import target_url_for
from .sus import polarity
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


def _numeric_survey_avg(survey: dict, reverse: frozenset | set = frozenset()) -> float | None:
    """The persona's mean 1-5 answer, negatively worded questions flipped (6 - v).

    Without the flip the mean mixes two directions: Julia's SUS answers average
    2,2 raw and 4,2 flipped, and the raw figure was narrated as "very low" for
    a participant whose SUS is 80. For a complete SUS the flipped mean is
    SUS / 25 + 1. `reverse` is the set of reversed question ids (sus.polarity).
    """
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
            vals.append(6 - f if a.get("question_id") in reverse else f)
    return round(sum(vals) / len(vals), 2) if vals else None


def reversed_ids(q_meta) -> frozenset:
    """The ids of the questionnaire's negatively worded questions."""
    return frozenset(qid for qid, rev in polarity(q_meta).items() if rev)


_INJECTED_BACK_MARKERS = _FALLBACK_MARKERS + ("zamknięta regułami badania",)


def _desc_has(action: dict, markers) -> bool:
    desc = str((action or {}).get("description") or "").lower()
    return any(m in desc for m in markers)


def _injected_back(action: dict) -> bool:
    return (action or {}).get("action") == "back" and _desc_has(action, _INJECTED_BACK_MARKERS)


def _has_stuck_run(actions: list, length: int = 3) -> bool:
    """Were `length` consecutive actions identical (scroll and refresh included)?"""
    run, prev = 0, None
    for a in actions:
        if not isinstance(a, dict):
            run, prev = 0, None
            continue
        key = (a.get("action"), a.get("target"), a.get("amount"), a.get("direction"))
        run = run + 1 if key == prev else 1
        prev = key
        if run >= length:
            return True
    return False


def compute_iso_metrics(store: TraceStore, q_meta: list | None = None) -> dict:
    """Return effectiveness / efficiency / satisfaction signals for one run.

    `q_meta` is the questionnaire's `questions`; it says which survey answers
    read the other way round. None (the default) averages every answer as given.
    """
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
    # The persona's own backs: the harness injects backs of its own (loop
    # escape, circuit breaker, refused-path exit), which were counted too.
    backtracks = sum(1 for a in actions if a.get("action") == "back" and not _injected_back(a))
    # A loop is a loop: three identical actions in a row, or one of our own
    # loop escapes. It used to be "any harness-suspect step", so a single
    # "Element 'IONIQ 5' not found" marked an ordinary configurator session
    # as looping (2 of 14 flagged sessions had no repeat of any kind).
    loop_detected = _has_stuck_run(actions) or any(
        a.get("action") == "back" and _desc_has(a, _FALLBACK_MARKERS) for a in actions)
    reached_step_limit = ended_reason == "max_steps"

    # --- satisfaction (SYNTHETIC self-report — never a real-user metric) ---
    synth_score = _numeric_survey_avg(survey, reversed_ids(q_meta))
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


def run_iso_metrics(store: TraceStore, q_meta: list | None = None) -> dict:
    """Compute and persist ISO 9241-11 metrics for a run (cheap sync pass)."""
    metrics = compute_iso_metrics(store, q_meta)
    store.write_iso_metrics(metrics)
    return metrics


def current_effectiveness(store: TraceStore) -> dict:
    """The effectiveness block as the current code computes it.

    iso_metrics.json is written once, during the session, so it keeps whatever
    the code of the day decided: e5d6's still says "observed: failed at
    order_summary_reached" — a car configurator judged against a checkout
    ladder, fixed in 36010ba — while the current code finds no measurable
    target. Recomputed whenever the session's trace is there (~17 ms); the
    stored block only when there is nothing to compute from.
    """
    if store.read_action_trace():
        return compute_iso_metrics(store).get("effectiveness") or {}
    return (store.read_iso_metrics() or {}).get("effectiveness") or {}


def effective_outcome(store: TraceStore) -> tuple[str, str]:
    """A participant's task outcome and where it comes from — the one definition
    the score, the report's objectives section, the Word file and the dashboard
    all count.

      "observed" — measured from the trace (session_goal_state)
      "session"  — the harness ended the session: persona_abandoned, after
                   several identical actions with no page change
      "declared" — the model's verdict on the session ("unknown" if none)

    They used to read different sources: the score used the measured outcome
    while the objectives section tallied the model's verdicts, so e5d6 printed
    "nieosiągnięty: 3/3, zmierzone" beside "osiągnięty 1 · częściowo 1 ·
    nieosiągnięty 1"; and the dashboard ignored an abandonment entirely.
    """
    session = store.read_session_result() or {}
    if str(session.get("ended_reason") or "") == "persona_abandoned":
        return "persona_abandoned", "session"
    # The tool crashed under the participant (a parse error, a failed write).
    # Whether they would have reached the goal is unknown, and "failed" would
    # charge the site for it: the 2026-10-13 run scored a crash as a failed task.
    if str(session.get("ended_reason") or "") == "error":
        return "unknown", "error"
    eff = current_effectiveness(store)
    if eff.get("outcome_source") == "observed" and eff.get("observed_outcome"):
        return str(eff["observed_outcome"]), "observed"
    refl = store.read_objective_reflection() or {}
    return str(refl.get("outcome") or "unknown"), "declared"
