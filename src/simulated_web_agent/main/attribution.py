"""Harness-vs-site attribution for audit findings.

The auditor LLM turns the agent's session into UX findings, but it cannot tell
the difference between "the site is broken" and "the agent failed to interact
with the site." Both look the same to it: an action that produced no visible
change. In practice a large share of severe findings trace back to *harness*
interaction failures — e.g. pepco.pl's Google Places store locator (typed text
never selected a suggestion) or a product search the agent submitted with Enter
that the site only submits on button click. Those got reported as CRITICAL site
defects ("store locator non-functional", "search doesn't process queries").

This module flags findings whose cited step coincides with a harness
interaction failure, so they can be excluded from the headline severity counts
and rendered as "needs verification" rather than shipped as confirmed defects.
"""
import json
import logging

from .trace_store import TraceStore

log = logging.getLogger(__name__)

# Substrings of the harness fallback descriptions emitted when an element/method
# "didn't work after several tries" (experiment.py). Their presence at a step is
# direct proof the interaction failed on the harness side, not the site side.
_FALLBACK_MARKERS = ("nie działał", "nie działała", "wybieram inną", "innej drogi")

# A target clicked/typed this many times or more is a stuck-interaction loop:
# the agent kept retrying because nothing changed — a harness failure signature.
_REPEAT_THRESHOLD = 3


def _parse_action(entry) -> dict:
    if isinstance(entry, str):
        try:
            return json.loads(entry)
        except Exception:
            return {"description": entry}
    return entry if isinstance(entry, dict) else {}


def harness_suspect_steps(store: TraceStore) -> set[int]:
    """Step indices whose action is a harness interaction failure.

    Two signatures, both deterministic:
      1. a fallback action ("...nie działał mimo kilku prób...") — and the step
         it retreated from, which is where the finding is usually anchored;
      2. an (action, target) pair repeated >= _REPEAT_THRESHOLD times — a stuck
         loop where the agent retried because the interaction had no effect.
    """
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    suspect: set[int] = set()

    for i, a in enumerate(actions):
        desc = (a.get("description") or "").lower()
        if any(m in desc for m in _FALLBACK_MARKERS):
            suspect.add(i)
            if i - 1 >= 0:
                suspect.add(i - 1)

    counts: dict[tuple, int] = {}
    for a in actions:
        if a.get("target"):
            key = (a.get("action"), a.get("target"))
            counts[key] = counts.get(key, 0) + 1
    for i, a in enumerate(actions):
        if a.get("target") and counts.get((a.get("action"), a.get("target")), 0) >= _REPEAT_THRESHOLD:
            suspect.add(i)

    return suspect


def _suspect_targets(store: TraceStore, suspect: set[int]) -> set[str]:
    """Element targets (parser-semantic-ids) involved in the failed interactions.
    Findings often anchor on a later step (e.g. the terminate) but name the
    element in their evidence text — e.g. Kacper's "pola wyszukiwania
    'wyszukaj_lokalizacje'" at step 28, after the loop ended at step 24."""
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    return {
        str(actions[i].get("target"))
        for i in suspect
        if i < len(actions) and actions[i].get("target")
    }


def flag_harness_suspects(store: TraceStore, findings: list) -> list:
    """Stamp each finding with `harness_suspect` / `requires_human_check` when it
    is attributable to a harness interaction failure — either its cited step
    lands on one, or its evidence text names an element that was stuck in one.
    Returns the same list (findings mutated in place); non-dicts pass through."""
    suspect = harness_suspect_steps(store)
    if not suspect:
        return findings
    targets = _suspect_targets(store, suspect)

    def _is_suspect(f: dict) -> bool:
        step = f.get("step")
        try:
            step = int(step)
        except (TypeError, ValueError):
            step = None
        if step is not None and step in suspect:
            return True
        text = f"{f.get('evidence', '')} {f.get('recommendation', '')}"
        return any(t and t in text for t in targets)

    for f in findings or []:
        if isinstance(f, dict) and _is_suspect(f):
            f["harness_suspect"] = True
            f["requires_human_check"] = True
    return findings
