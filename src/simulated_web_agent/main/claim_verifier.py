"""Claim verification — the truth gate for heuristic findings.

Why this exists (§5.1 ground truth): the diabetyk24.pl team manually verified
the 5-persona report and found ~2 of 8 issues real. The disproven ones were
refutable from the run's own trace: the "product click → login redirect"
critical cited a step whose observation shows a filter panel and no login form;
"no cart icon" cited a step whose observation *contains* the cart's
`aria-label="Koszyk, liczba produktów: 1"`; "the page is empty" cited steps
whose raw HTML was tens of KB. The heuristic auditor never sees the page — only
the agent's narrative, truncated to 400 chars per step — and `verified: True`
(evidence.py) means only "the cited step exists". Nothing compared claim to
evidence. This pass does.

For each finding that survived the deterministic gates (not harness_suspect,
not off_site, verified), one audit-tier LLM call per persona re-reads the
cited step's FULL observation text plus the neighbouring actions and renders a
verdict:

    supported    — the observation shows the claimed problem
    partial      — related friction is visible but the claim overstates it
    unsupported  — the observation does not show it (or shows the opposite)

Consequences are applied deterministically downstream: `unsupported` is
excluded from headline counts / aggregate / prioritization exactly like
harness_suspect (severity.excluded_from_report); `partial` caps confidence to
"medium" (confidence._ceiling); `supported` changes nothing.

Fail-open by contract: any verifier failure (LLM error, malformed reply,
unknown verdict) leaves findings untouched. An unverified report is honest;
a silently emptied one is not.
"""
from __future__ import annotations

import json
import logging

from ..agent.gpt import _parse_json_response, async_chat
from ..agent.prompt_registry import registry as _prompts
from .trace_store import TraceStore

log = logging.getLogger(__name__)

_VALID_VERDICTS = {"supported", "partial", "unsupported"}

# Full observation, but bounded: a pathological transcript must not blow the
# prompt (the a11y pass once built a 1.66M-char prompt this way).
_MAX_OBS_CHARS = 4000
_MAX_REASON_CHARS = 300


def _parse_action(entry) -> dict:
    if isinstance(entry, str):
        try:
            entry = json.loads(entry)
        except Exception:
            return {"description": entry}
    return entry if isinstance(entry, dict) else {}


def _step_context(store: TraceStore, step) -> dict:
    """The cited step's full observation + the actions just before/after it."""
    try:
        step = int(step)
    except (TypeError, ValueError):
        return {"observation_full": "", "action_before": "", "action_after": ""}
    obs = store.read_observation_step_text(step).strip()
    if len(obs) > _MAX_OBS_CHARS:
        obs = obs[:_MAX_OBS_CHARS] + "…"
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]

    def _desc(i):
        if 0 <= i < len(actions):
            return (actions[i].get("description") or "")[:200]
        return ""

    return {
        "observation_full": obs,
        "action_before": _desc(step - 1),
        "action_at_step": _desc(step),
        "action_after": _desc(step + 1),
    }


async def run_claim_verification(store: TraceStore, findings: list) -> list:
    """Verdict every verifiable finding against its cited evidence.

    Mutates and returns `findings`. Idempotent: stale claim_verdict fields are
    cleared first, so re-running after a rule change never leaves a finding
    carrying a verdict it would no longer receive.
    """
    # Clear stale verdicts on everything up front (fail-open then re-stamps).
    for f in findings or []:
        if isinstance(f, dict):
            f.pop("claim_verdict", None)
            f.pop("verdict_reason", None)

    # Only findings still standing after the deterministic gates: the others
    # are already excluded/capped — no LLM cost re-judging them.
    to_verify: list[tuple[int, dict]] = [
        (i, f) for i, f in enumerate(findings or [])
        if isinstance(f, dict)
        and not f.get("harness_suspect")
        and not f.get("off_site")
        and f.get("verified", True)
    ]
    if not to_verify:
        return findings

    payload = []
    for local_id, (_, f) in enumerate(to_verify):
        ctx = _step_context(store, f.get("step"))
        payload.append({
            "id": local_id,
            "heuristic_id": f.get("heuristic_id"),
            "severity": f.get("severity"),
            "claim": f.get("evidence", ""),
            "step": f.get("step"),
            "url": f.get("url", ""),
            **ctx,
        })

    prompt = _prompts.get("verify_findings").replace(
        "{findings_json}", json.dumps(payload, ensure_ascii=False, indent=1)
    )

    try:
        raw = await async_chat(
            [{"role": "user", "content": prompt}], model="report", log=False
        )
        result = json.loads(_parse_json_response(raw))
        verdicts = result.get("verdicts") if isinstance(result, dict) else None
        if not isinstance(verdicts, list):
            raise ValueError("no verdicts array in verifier reply")
    except Exception as e:
        # Fail open: an unverified report beats a silently emptied one.
        log.warning("[claim_verifier] verification failed, findings left "
                    "unchanged: %s", e)
        return findings

    by_id = {}
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        verdict = str(v.get("verdict", "")).strip().lower()
        if verdict not in _VALID_VERDICTS:
            continue  # a creative verdict string must not exclude a finding
        try:
            by_id[int(v.get("id"))] = (verdict, str(v.get("reason", ""))[:_MAX_REASON_CHARS])
        except (TypeError, ValueError):
            continue

    stamped = 0
    for local_id, (_, f) in enumerate(to_verify):
        if local_id in by_id:
            verdict, reason = by_id[local_id]
            f["claim_verdict"] = verdict
            f["verdict_reason"] = reason
            stamped += 1

    counts = {}
    for verdict, _ in by_id.values():
        counts[verdict] = counts.get(verdict, 0) + 1
    log.info("[claim_verifier] %d/%d finding(s) verdicted: %s",
             stamped, len(to_verify), counts)
    return findings
