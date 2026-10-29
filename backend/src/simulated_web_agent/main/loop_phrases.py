"""The sentences the harness writes when it forces the agent out of a stuck loop.

experiment.py emits them; attribution.py and iso_metrics.py recognise them. They
used to be written out in three places, and narrowing the markers in one of them
stopped recognising the other sentence: there are two, worded differently
("Ta metoda nie działała…" for the loop-breaker, "Ten element nie działał…" for
the circuit breaker), and the test fed only one.

The same table now writes and reads them. An injected action carries its kind
and the action the persona had chosen, so a reader no longer has to guess from
wording which steps never ran as chosen; traces written before that carry only
the sentence, and `injected_kind` still reads those.
"""
from __future__ import annotations

from typing import Optional

from .trace_store import parse_action

FORCED_BACK_METHOD = "Ta metoda nie działała mimo kilku prób — wracam, by spróbować innej drogi."
FORCED_BACK_ELEMENT = "Ten element nie działał mimo kilku prób — wracam i wybieram inną drogę."

# The audit's own rules closed this path (no personal data, stay on the site): a
# policy working, not a failure. The sentence must stay clear of the markers
# below, or attribution would read it as a harness failure and discard the
# findings anchored on the step.
REFUSED_DESCRIPTION = "Ta ścieżka jest zamknięta regułami badania — wracam i kontynuuję zadanie."

_METHOD_MARKER = "nie działała mimo kilku prób"
_ELEMENT_MARKER = "nie działał mimo kilku prób"

# Lower case, as descriptions are compared. Whole phrases, not fragments of them:
# a persona's own "wybieram inną wersję wyposażenia" is not a tool failure.
FORCED_BACK_MARKERS = (_METHOD_MARKER, _ELEMENT_MARKER)

KIND_LOOP_BREAKER = "loop_breaker"
KIND_FAILED_BREAKER = "failed_breaker"
KIND_REFUSED = "refused"

_SENTENCES = {
    KIND_LOOP_BREAKER: FORCED_BACK_METHOD,
    KIND_FAILED_BREAKER: FORCED_BACK_ELEMENT,
    KIND_REFUSED: REFUSED_DESCRIPTION,
}
# The kinds that say the tool could not do what was asked. A refusal is not one:
# it is the audit's own rule working.
_TOOL_TROUBLE = (KIND_LOOP_BREAKER, KIND_FAILED_BREAKER)


def injected_kind(action) -> Optional[str]:
    """Which harness replacement an action_trace entry is, or None.

    Reads the marker `injected_action` writes; an entry from before the marker
    is told by its sentence."""
    a = parse_action(action)
    kind = a.get("harness")
    if kind in _SENTENCES:
        return kind
    desc = str(a.get("description") or "")
    low = desc.lower()
    if _METHOD_MARKER in low:
        return KIND_LOOP_BREAKER
    if _ELEMENT_MARKER in low:
        return KIND_FAILED_BREAKER
    if desc.strip() == REFUSED_DESCRIPTION:
        return KIND_REFUSED
    return None


def is_injected(action) -> bool:
    return injected_kind(action) is not None


def is_tool_trouble_injection(action) -> bool:
    return injected_kind(action) in _TOOL_TROUBLE


def intended_action(action) -> Optional[dict]:
    """What the persona had chosen before the harness replaced it; None for an
    ordinary action, and for a trace written before the harness kept it."""
    intended = parse_action(action).get("intended")
    return intended if isinstance(intended, dict) else None


def injected_action(kind: str, chosen) -> dict:
    """The 'back' the harness runs in place of `chosen`, the persona's own pick.

    `chosen` may itself be an injection (the failed-action breaker looking at a
    loop-breaker's back): what is kept is the persona's choice, never a harness
    step."""
    if kind not in _SENTENCES:
        raise ValueError(f"unknown harness injection: {kind!r}")
    return {
        "action": "back",
        "description": _SENTENCES[kind],
        "harness": kind,
        "intended": dict(intended_action(chosen) or parse_action(chosen)),
    }
