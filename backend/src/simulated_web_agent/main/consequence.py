"""Cap a finding's severity at what the participant observably lost.

The auditor LLM labels a finding "poważny" because a path is longer than it
should be, whatever happened next. Two audits of the same trace gave the same
finding ("the mortgage page has no link to the simulator", Marta, step 4) as
minor and as serious: she used the site search, reached the simulator four steps
later and completed the simulation, with no failed action — and the serious label
cost 0.2 of the score. Severity is what the participant lost, and the trace says
what that was:

  blocked        the session ended without reaching the goal, or the same control
                 failed twice right after the finding, or the finding is one of
                 the last steps of a session that did not succeed
  failed_actions actions the tool could not carry out in the next five steps, on
                 the page or control the finding is about
  detour_steps   steps until the participant left that page (informational)

The ceiling: "krytyczny" needs `blocked`; "poważny" needs `blocked`, a second
participant reporting the same issue, or two failed actions; everything else is
"drobny". A consequence that cannot be computed (legacy trace, no step, an outcome
nobody measured) never lowers anything. Pure given the store, and idempotent: the
label before the cap is kept and the cap is recomputed from it.
"""
import logging
import re

from .attribution import _action_control, _stems4, tool_failed_actions
from .iso_metrics import effective_outcome
from .severity import excluded_from_report, heuristic_id, severity_rank
from .textutil import view_key
from .trace_store import TraceStore, parse_action

log = logging.getLogger(__name__)

WINDOW = 5
LAST_STEPS = 3
DETOUR_CAP = 10

CAPPED_BY = "task_completed_no_failure"
CAPPED_BY_OTHER = "no_blocking_consequence"

_BLOCKED_OUTCOMES = frozenset({"failed", "persona_abandoned"})
_LABELS = ("drobny", "poważny", "krytyczny")

# What the report says under a lowered label.
NOTE = ("Istotność obniżona do „drobny”: po tym kroku uczestnik nie został "
        "zablokowany i nie nastąpiła nieudana akcja, a uwagę zgłosił jeden uczestnik.")


def _path(url) -> str:
    return view_key(url).split("#")[0]


def consequence_record(store: TraceStore, finding: dict) -> dict | None:
    """{"blocked", "failed_actions", "detour_steps", "outcome"} or None when unknown."""
    try:
        step = finding.get("step")
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            return None
        actions = [parse_action(a) for a in (store.read_action_trace() or [])]
        if not actions or step >= len(actions):
            return None
        outcome, _ = effective_outcome(store)
        if outcome not in ("success", "partial", "failed", "persona_abandoned"):
            return None

        urls = store.step_urls()
        page = _path(finding.get("url") or (urls[step] if step < len(urls) else ""))
        text_stems = _stems4(finding.get("evidence"))
        failed = tool_failed_actions(store)
        hits = []
        for k in range(step, min(step + WINDOW + 1, len(actions))):
            if k not in failed:
                continue
            target = str(actions[k].get("target") or "")
            same_page = bool(page) and k < len(urls) and _path(urls[k]) == page
            names_it = len(_stems4(target) & text_stems) >= 2
            if same_page or names_it:
                hits.append(k)
        controls = [_action_control(actions[k]) or str(actions[k].get("target") or "") for k in hits]
        repeated_control = any(c and controls.count(c) >= 2 for c in controls)

        detour = 0
        if page:
            for k in range(step, min(step + DETOUR_CAP, len(urls))):
                if _path(urls[k]) != page:
                    break
                detour += 1
        blocked = (
            outcome in _BLOCKED_OUTCOMES
            or repeated_control
            or (outcome != "success" and step >= len(actions) - LAST_STEPS)
        )
        return {"blocked": bool(blocked), "failed_actions": len(hits),
                "detour_steps": detour, "outcome": outcome}
    except Exception:
        log.exception("[consequence] could not compute a consequence record")
        return None


def severity_ceiling(consequence: dict | None, corroborated: bool = False) -> str | None:
    """The highest label the consequence supports, None when it does not limit."""
    if not consequence:
        return None
    if consequence.get("blocked"):
        return None
    if corroborated or int(consequence.get("failed_actions") or 0) >= 2:
        return "poważny"
    return "drobny"


def corroborated_findings(findings: list) -> set[int]:
    """Indexes of findings another participant also reports: same heuristic, same page.

    `findings` carry `_persona_dir`, as the aggregate annotates them."""
    groups: dict[tuple, set] = {}
    for f in findings:
        if isinstance(f, dict) and not excluded_from_report(f):
            groups.setdefault((heuristic_id(f), _path(f.get("url"))), set()).add(f.get("_persona_dir"))
    return {i for i, f in enumerate(findings)
            if isinstance(f, dict) and not excluded_from_report(f)
            and len(groups[(heuristic_id(f), _path(f.get("url")))]) >= 2}


def apply_severity_ceiling(store: TraceStore | None, findings: list,
                           corroborated: set[int] | None = None, offset: int = 0) -> list:
    """Cap each finding's severity; keeps severity_before_cap / severity_capped_by.

    `corroborated` holds indexes (shifted by `offset`) into the caller's own list;
    without it a finding counts as reported by one participant. Mutates in place."""
    for i, f in enumerate(findings or []):
        if not isinstance(f, dict):
            continue
        base = f.get("severity_before_cap") or f.get("severity")
        if base not in _LABELS or store is None:
            continue
        record = consequence_record(store, f)
        ceiling = severity_ceiling(record, (offset + i) in (corroborated or ()))
        f["severity"] = base
        f.pop("severity_before_cap", None)
        f.pop("severity_capped_by", None)
        f.pop("severity_note", None)
        if ceiling and severity_rank(base) > severity_rank(ceiling):
            f["severity"] = ceiling
            f["severity_before_cap"] = base
            f["severity_capped_by"] = CAPPED_BY if record.get("outcome") == "success" else CAPPED_BY_OTHER
            f["severity_note"] = NOTE
        if record is not None:
            f["consequence"] = record
    return findings
