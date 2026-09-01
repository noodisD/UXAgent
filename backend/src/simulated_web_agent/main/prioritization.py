"""Severity x impact x confidence prioritization (METHODOLOGY_PLAN §6.2).

Turns confirmed findings into a ranked matrix the client can act on.

Impact is derived, not asked for
────────────────────────────────
The aggregate prompt already emits an `impact` field, but it is a paragraph of
prose — real information, and useless as a sort key. The obvious fix is to ask
the model for low/medium/high instead. Don't: that would be the fourth
self-report in this codebase to collapse. first_impression returned the prompt's
example scores on 71% of 38 scans, `confidence` said "high" on 56 of 59 findings
and never once said "low", and accessibility returned the example's `3` on 62%
of scans. A model rating its own output does not discriminate.

The trace already answers the question:

    impact = reach x task consequence
      reach   — how many of the run's personas reported this finding (counted
                from the findings themselves)
      blocked — whether any persona who reported it failed or abandoned the task

    high    widespread AND blocking
    medium  widespread OR blocking
    low     neither

The `outcomes` this receives are now the OBSERVED per-persona outcomes
(session_goal_state via iso_metrics, §4.2), not the LLM's self-rating — so
"blocked" keys off evidence that the persona did not reach the goal, not an
opinion. It caught the LLM declaring "failed" on a run whose trace shows the
persona reached the order summary.

Known weakness that remains, stated because the report must not hide it:
`blocked` asks whether a persona who reported the finding failed — not whether
*this finding* caused the failure. A persona who failed for an unrelated reason
lends "blocked" to every finding they filed. Tightening it needs a causal signal
we still do not have (evidence tells us they failed, not why). So every row ships
`personas_affected`, `participant_count` and `blocked_personas` alongside the
label: a reader can apply their own rule, or disagree with this one, instead of
taking "high" on faith.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from .severity import excluded_from_report, heuristic_id as _heuristic_id, severity_score

log = logging.getLogger(__name__)

# "Widespread" = at least half the panel AND more than one person. The ratio
# alone is degenerate at the sizes this tool runs: with 2 personas a single one
# is 50%, so every finding scored widespread and the whole matrix came back
# "high" — the exact non-discrimination this module exists to avoid. One person
# hitting something is never evidence of reach, whatever the arithmetic says.
_WIDESPREAD_RATIO = 0.5
_WIDESPREAD_MIN_PERSONAS = 2

_IMPACT_RANK = {"high": 2, "medium": 1, "low": 0}
_CONFIDENCE_RANK = {"high": 2, "medium": 1, "low": 0}

# Outcomes that mean the persona did not get their job done. `persona_abandoned`
# is an ended_reason rather than an outcome, but it is the same fact.
_BLOCKED_OUTCOMES = {"failed", "persona_abandoned"}

_SEVERITY_ORDER = {"krytyczny": 3, "poważny": 2, "drobny": 1}


def derive_impact(*, personas_affected: int, participant_count: int,
                  blocked: bool) -> str:
    """Impact from reach and task consequence. See module docstring."""
    if participant_count <= 0:
        return "low"
    widespread = (
        personas_affected >= _WIDESPREAD_MIN_PERSONAS
        and (personas_affected / participant_count) >= _WIDESPREAD_RATIO
    )
    if widespread and blocked:
        return "high"
    if widespread or blocked:
        return "medium"
    return "low"


def prioritize(findings: list, outcomes: dict[str, str]) -> list[dict]:
    """Rank confirmed findings into §6.2 matrix rows, worst first.

    `findings` are aggregate-level dicts carrying `_persona_dir`; `outcomes` maps
    persona_dir -> that persona's outcome (or ended_reason). Harness-suspect
    findings are dropped: they are not confirmed defects, and ranking a harness
    bug against real findings is how a false CRITICAL reaches a client.

    Returns one row per heuristic — OR one row per root_cause_id (root_cause.py)
    when the finding has been cross-heuristic clustered, e.g. the same "search
    doesn't refresh results" defect filed under 6 different heuristics collapses
    to one row instead of ranking as 6 independent criticals. Each row carries
    its impact evidence and a 1-based `priority`.
    """
    groups: dict = defaultdict(list)
    for f in findings or []:
        # Gated findings (harness failures, off-site pages, verifier-rejected
        # claims) never enter the matrix — ranking a false claim against real
        # findings is how a false CRITICAL reaches a client.
        if not isinstance(f, dict) or excluded_from_report(f):
            continue
        # root_cause_id (str) if the cross-heuristic clustering pass tagged it,
        # else heuristic_id (int) — a mixed-type key is fine, each finding only
        # ever lands in one bucket.
        groups[f.get("root_cause_id") or _heuristic_id(f)].append(f)

    participant_count = len(outcomes or {})
    rows: list[dict] = []

    for key, group in groups.items():
        is_cluster = isinstance(key, str)
        personas = {f.get("_persona_dir") for f in group if f.get("_persona_dir")}
        blocked_personas = sorted(
            p for p in personas
            if str(outcomes.get(p, "")).strip().lower() in _BLOCKED_OUTCOMES
        )
        worst = max(group, key=lambda f: _SEVERITY_ORDER.get(f.get("severity"), 0))
        # The group's weakest confidence: one shaky finding should not inherit a
        # sibling's certainty.
        confidence = min(
            (f.get("confidence") or "medium" for f in group),
            key=lambda c: _CONFIDENCE_RANK.get(c, 1),
        )
        impact = derive_impact(
            personas_affected=len(personas),
            participant_count=participant_count,
            blocked=bool(blocked_personas),
        )
        row = {
            # Kept as an int for frontend/back-compat even for a cluster row —
            # the lowest heuristic_id it was filed under; heuristics_involved
            # below carries the full cross-heuristic picture.
            "heuristic_id": min((_heuristic_id(f) for f in group
                                 if _heuristic_id(f)), default=0),
            "heuristic_name": worst.get("root_cause_name") if is_cluster
                              else worst.get("heuristic_name", ""),
            "severity": worst.get("severity"),
            "severity_score": severity_score(worst),
            "impact": impact,
            "confidence": confidence,
            # The evidence for `impact`, so the rule can be checked, not trusted.
            "personas_affected": len(personas),
            "participant_count": participant_count,
            "blocked": bool(blocked_personas),
            "blocked_personas": blocked_personas,
            "finding_count": len(group),
        }
        if is_cluster:
            row["root_cause_id"] = key
            row["heuristics_involved"] = sorted(
                {_heuristic_id(f) for f in group if _heuristic_id(f)})
        rows.append(row)

    rows.sort(key=lambda r: (
        r["severity_score"],
        _IMPACT_RANK.get(r["impact"], 0),
        _CONFIDENCE_RANK.get(r["confidence"], 1),
    ), reverse=True)
    for i, r in enumerate(rows, 1):
        r["priority"] = i

    log.info("[prioritization] %d matrix row(s) from %d finding(s), %d participant(s)",
             len(rows), len(findings or []), participant_count)
    return rows
