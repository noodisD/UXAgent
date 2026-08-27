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

# The same (action, target) repeated this many times *consecutively* is a stuck
# loop: the agent retried back-to-back because nothing changed.
#
# It must be consecutive. Counting repeats across the whole session flags normal
# behaviour — in run 2026-07-16_13-41-14 Marcin typed six different queries into
# the same search box and Michał revisited one category three times; neither is a
# loop, yet the session-wide rule flagged 7 of Marcin's 9 real findings as
# "needs verification" and dropped them from the headline counts.
_REPEAT_THRESHOLD = 3


def _parse_action(entry) -> dict:
    if isinstance(entry, str):
        try:
            return json.loads(entry)
        except Exception:
            return {"description": entry}
    return entry if isinstance(entry, dict) else {}


# Perception-artifact signature: the observation text says (almost) nothing
# while the step's raw HTML is large — the perceiver failed on an overlay
# state; the page was not actually blank. Three "The page is empty" CRITICAL
# findings in the diabetyk24 ground-truth run were exactly this.
_ARTIFACT_OBS_MAX_CHARS = 80
_ARTIFACT_RAW_MIN_BYTES = 20_000


def harness_suspect_steps(store: TraceStore) -> set[int]:
    """Step indices whose action is a harness interaction failure.

    Three signatures, all deterministic:
      1. a fallback action ("...nie działał mimo kilku prób...") — and the step
         it retreated from, which is where the finding is usually anchored;
      2. an (action, target) pair repeated >= _REPEAT_THRESHOLD times — a stuck
         loop where the agent retried because the interaction had no effect;
      3. a near-empty observation over a large raw page — a perception-capture
         artifact ("The page is empty" while raw_html is tens of KB), i.e. the
         perceiver failed, not the site.
    """
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    suspect: set[int] = set()

    for i in range(len(actions)):
        obs = store.read_observation_step_text(i).strip()
        if obs and len(obs) <= _ARTIFACT_OBS_MAX_CHARS:
            if store.raw_html_size(i) >= _ARTIFACT_RAW_MIN_BYTES:
                suspect.add(i)

    for i, a in enumerate(actions):
        desc = (a.get("description") or "").lower()
        if any(m in desc for m in _FALLBACK_MARKERS):
            suspect.add(i)
            if i - 1 >= 0:
                suspect.add(i - 1)

    # Consecutive runs of the same (action, target).
    run_start = 0
    for i in range(1, len(actions) + 1):
        prev = actions[i - 1]
        same = (
            i < len(actions)
            and prev.get("target")
            and actions[i].get("target") == prev.get("target")
            and actions[i].get("action") == prev.get("action")
        )
        if same:
            continue
        run_len = i - run_start
        if run_len >= _REPEAT_THRESHOLD and prev.get("target"):
            suspect.update(range(run_start, i))
        run_start = i

    return suspect


# Phrases a finding uses to claim the product search itself failed. If the
# trace proves the search *did* run (reached a /search results URL), such a
# claim is contradicted by the agent's own navigation and is harness-suspect.
_SEARCH_FAIL_MARKERS = (
    "nie przetwarza", "nie rozumie", "nie zwraca", "nie wyświetl",
    "brak wyników", "wyszukiwarka nie", "wyszukiwarki nie",
    "nie przetworzy", "nie zadział",
)


def _search_ran(store: TraceStore) -> bool:
    """True if the session ever reached a product-search results URL."""
    try:
        urls = store.visited_urls()
    except Exception:
        urls = []
    return any("/search" in (u or "").lower() for u in urls)


def _claims_search_broken(finding: dict) -> bool:
    text = f"{finding.get('evidence', '')} {finding.get('recommendation', '')}".lower()
    return "wyszukiwar" in text and any(m in text for m in _SEARCH_FAIL_MARKERS)


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
    targets = _suspect_targets(store, suspect)
    # If the search demonstrably ran, any "search is broken" claim is
    # contradicted by the agent's own navigation.
    search_contradicted = _search_ran(store)

    def _is_suspect(f: dict) -> bool:
        step = f.get("step")
        try:
            step = int(step)
        except (TypeError, ValueError):
            step = None
        if step is not None and step in suspect:
            return True
        text = f"{f.get('evidence', '')} {f.get('recommendation', '')}"
        if any(t and t in text for t in targets):
            return True
        if search_contradicted and _claims_search_broken(f):
            return True
        return False

    # Idempotent: always write both outcomes, so re-running after a rule change
    # clears stale flags instead of leaving them latched on.
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        suspected = _is_suspect(f)
        f["harness_suspect"] = suspected
        f["requires_human_check"] = suspected
    return findings
