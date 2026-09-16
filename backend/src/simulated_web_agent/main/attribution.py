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
import logging
import re

from .trace_store import TraceStore, parse_action as _parse_action

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




# Perception-artifact signature: the observation text says (almost) nothing
# while the step's raw HTML is large — the perceiver failed on an overlay
# state; the page was not actually blank. Three "The page is empty" CRITICAL
# findings in the diabetyk24 ground-truth run were exactly this.
_ARTIFACT_OBS_MAX_CHARS = 80
_ARTIFACT_RAW_MIN_BYTES = 20_000

# Errors the executor writes when it could not carry out an action at all
# (executor/env.py). Their presence is direct proof the failure was ours: a
# genuine site error — a 404, a validation message, a dead button — arrives as
# page *content*, never through this channel.
#
# Deliberately NOT included: the repeat- and revisit-advisories that
# experiment.py injects into the same field. Those say the agent retried
# something, which is just as likely to mean the *site* did not respond — the
# exact real defect this module must not swallow. Consecutive repeats are
# already covered, more conservatively, by _REPEAT_THRESHOLD below.
_EXECUTOR_ERROR_MARKERS = (
    "error executing action:",
    "invalid json action format:",
    "missing required parameter in action:",
)


def harness_suspect_steps(store: TraceStore) -> set[int]:
    """Step indices whose action is a harness interaction failure.

    Four signatures, all deterministic:
      1. a fallback action ("...nie działał mimo kilku prób...") — and the step
         it retreated from, which is where the finding is usually anchored;
      2. an (action, target) pair repeated >= _REPEAT_THRESHOLD times — a stuck
         loop where the agent retried because the interaction had no effect;
      3. a near-empty observation over a large raw page — a perception-capture
         artifact ("The page is empty" while raw_html is tens of KB), i.e. the
         perceiver failed, not the site;
      4. the executor reported it could not perform the action at all.
    """
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    suspect: set[int] = set()

    # (4) Executor failures. observations[i] is the state recorded *before*
    # action i (experiment.py appends at the top of the loop), so the error it
    # carries was produced by action i-1 — that is the step to blame. Step i is
    # added too: the agent perceives the un-changed page there, and that is
    # usually where the resulting finding anchors.
    #
    # This is the strongest harness-vs-site signal in the run and it was written
    # to disk every session while nothing read it. It is what turned pepco.pl's
    # consent overlay into "critical site defects".
    for i, obs in enumerate(store.read_observations() or []):
        if not isinstance(obs, dict):
            continue
        err = str(obs.get("error") or "").lower()
        if any(m in err for m in _EXECUTOR_ERROR_MARKERS):
            if i - 1 >= 0:
                suspect.add(i - 1)
            suspect.add(i)

    # (5) A consent notice was on screen and would not go away, so every
    # interaction from that point may have been intercepted by the overlay.
    # Nothing downstream could see this before: dismiss_cookie_banners returned
    # a bare bool and recorded nothing.
    try:
        unresolved = store.unresolved_consent_steps()
    except Exception:
        unresolved = set()
    if unresolved:
        n_steps = len(actions)
        for start in sorted(unresolved):
            suspect.update(range(start, n_steps))

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
    targets = {
        str(actions[i].get("target"))
        for i in suspect
        if i < len(actions) and actions[i].get("target")
    }
    # Ids shorter than this carry no signal: matched against Polish prose, a
    # target like "1", "q" or "cart" hits unrelated findings and demotes them to
    # harness_suspect, which zeroes severity_score, drops them from headline
    # counts and the prioritization matrix, and caps confidence to low. The
    # match is only meaningful for distinctive parser ids ("wyszukaj_lokalizacje").
    return {t for t in targets if len(t) >= _MIN_TARGET_LEN}


_MIN_TARGET_LEN = 5


def _names_target(text: str, targets: set[str]) -> bool:
    """Does the finding's prose name one of these elements?

    Whole-token match, not a substring: these are identifiers quoted inside the
    text ("pola wyszukiwania 'wyszukaj_lokalizacje'"), so a bare `t in text`
    also fired on any word that merely contained the id. Unlike the Polish stem
    markers in session_goal_state, a full boundary is correct here — an
    identifier is not inflected.
    """
    return any(
        re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text, re.IGNORECASE)
        for t in targets
    )


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
        if _names_target(text, targets):
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
        # Two keys, not one, and they are not synonyms even though this writer
        # sets both from the same bool:
        #   harness_suspect      — we caused it, so it is probably not a defect.
        #   requires_human_check — nobody can settle it from the trace alone.
        # Everything harness-suspect needs a human, hence the same value here,
        # but the converse does not hold: viewport_scan flags drag affordances
        # requires_human_check without any harness failure. That is why
        # severity.severity_score ORs the two rather than reading one.
        f["requires_human_check"] = suspected
    return findings
