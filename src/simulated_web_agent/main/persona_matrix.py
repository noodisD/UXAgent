"""Persona x task matrix for the report (METHODOLOGY_PLAN §6.1).

One row per persona: who they were, what they were asked to do, whether they got
it done, and what it cost them. Every column is computed elsewhere — this only
arranges them into the section §6.1 asks for.

It exists to stop a reader treating "3.2 / 5" as a property of the site. It is an
average over a handful of synthetic people, and the matrix is where that becomes
visible: two personas, both 28-year-old men from different towns, is a fact about
the panel that no aggregate score can express.

`outcome_source: "declared"` is not decoration. iso_metrics derives effectiveness
from `objective_reflection.outcome` — an LLM's verdict on the session it just ran
— and nothing checks it (§4.2). The matrix is exactly where a reader would
otherwise read it as an observation.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _get(d, *path, default=None):
    """Walk nested dicts without exploding on a missing or malformed level."""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def build_persona_task_matrix(personas: list) -> list[dict]:
    """Build §6.1's persona x task rows from the dashboard persona payloads.

    Missing metrics stay None rather than collapsing to 0: a persona whose
    analysis failed has no step count, and "0 steps, 0 backtracks" would read as
    a flawless run.
    """
    rows: list[dict] = []
    for p in personas or []:
        if not isinstance(p, dict):
            continue
        iso = p.get("iso") if isinstance(p.get("iso"), dict) else {}
        rows.append({
            "persona": p.get("name") or "",
            "home_location": p.get("homeLocation") or "",
            "task": p.get("objective") or "",
            "outcome": p.get("outcome"),
            # The LLM's verdict on its own run — see module docstring.
            "outcome_source": "declared",
            "goal_state_reached": _get(iso, "effectiveness", "goal_state_reached"),
            "steps": p.get("steps") if p.get("steps") is not None
                     else _get(iso, "efficiency", "step_count"),
            "backtracks": _get(iso, "efficiency", "backtracks"),
            "seq": _get(iso, "satisfaction", "synthetic_seq_1_7"),
            # Satisfaction is a synthetic self-report and §1.1 requires it to say
            # so wherever it is shown.
            "seq_source": _get(iso, "satisfaction", "label", default="synthetic_self_report"),
        })
    log.info("[persona_matrix] %d row(s)", len(rows))
    return rows
