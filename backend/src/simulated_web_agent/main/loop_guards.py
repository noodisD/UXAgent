"""The failed-action circuit breaker, apart from the browser loop.

It used to be a closure in experiment.py around one session-wide Counter keyed by
the whole action, and it went wrong three ways in a real run:

- once an action had failed twice, every later identical action was replaced by a
  forced back for the rest of the session. Kacper's store-locator `type` failed
  twice; each time he returned to that page, a fresh page, his first `type` was
  replaced (three wasted cycles);
- the key held the `enter` flag and the text, so `type enter=false` then
  `enter=true` were two different actions and the breaker never fired for Julia;
- the thought it wrote into the persona's memory called the element broken, and
  that wording surfaced in surveys and reflections as a defect of the site when
  all the session had seen was the tool failing.

Nothing here touches a browser, so the rules can be tested without one.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Optional

from ..executor.env import INPUT_REFUSAL_PREFIX, SCOPE_REFUSAL_PREFIX
from .textutil import view_key
from .trace_store import parse_action

# What a retry cannot change: the persona's own wording of the step, and whether
# it pressed Enter after typing.
_VOLATILE_KEYS = frozenset({"description", "enter"})

# Actions aimed at one control. The same control is the same attempt whatever was
# typed or chosen: three different strings into a field that will not take input
# are one failure, not three.
_TARGETED = frozenset({"click", "type", "select", "clear", "hover"})

# A dead address is dead from every page, so goto_url failures are counted over
# the whole session, not per page.
_GOTO = "goto_url:"

VERDICT_REFUSED = "refused"
VERDICT_FAILED = "failed"

# Failures on one visit to a page, and on one page over the whole session. A
# visit ends when the page changes; the page total is what stops a persona who
# keeps coming back to the same dead control.
FAILS_PER_VISIT = 2
FAILS_PER_PAGE = 4
REFUSALS_PER_ACTION = 2

# The persona's own memory of a blocked action. It says what happened to the
# tool, not what is wrong with the site: from "the tool could not complete it"
# the persona cannot tell the two apart, and neither can the report.
FAILED_THOUGHT = (
    "I tried this {tries} times ({what}) and the tool could not complete it. "
    "I cannot tell whether the site or the way I operate it is at fault, so I "
    "will not call it a defect unless I can see the problem on the page itself; "
    "I will go back and approach my goal another way."
)


# A path the audit's own rules closed (no personal data, stay on the site) is a
# policy working, not a failure, so neither sentence may blame the site; and the
# description must stay clear of the markers attribution reads as a harness
# failure, or the step's own findings would be discarded.
REFUSED_THOUGHT = (
    "This path is closed by the rules of this study (no personal "
    "data, stay on the site under test), not by the site — it is "
    "not a problem with the site. I will not try it again and will "
    "continue the task another way."
)
REFUSED_DESCRIPTION = "Ta ścieżka jest zamknięta regułami badania — wracam i kontynuuję zadanie."


def action_key(action_json: str) -> str:
    """Action identity ignoring the freeform description and the Enter flag."""
    a = {k: v for k, v in parse_action(action_json).items() if k not in _VOLATILE_KEYS}
    return json.dumps(a, sort_keys=True) if a else str(action_json)


def failure_key(action_json: str) -> str:
    """The attempt an action is a retry of: target (or address), not payload."""
    a = parse_action(action_json)
    name = a.get("action")
    if isinstance(name, str) and name in _TARGETED:
        return f"{name}:{a.get('target')}"
    if name == "goto_url":
        return f"{_GOTO}{view_key(a.get('url'))}"
    if name == "key_press":
        return f"key_press:{a.get('key')}:{a.get('target')}"
    if name == "raw_type":
        # No target to key on; the text is exactly what a retry varies.
        return "raw_type"
    return action_key(action_json)


def current_url(obs: dict) -> str:
    """The active tab's address; the first tab when none is marked active."""
    tabs = (obs or {}).get("tabs") or []
    for tab in tabs:
        if tab.get("is_active"):
            return tab.get("url", "")
    return tabs[0].get("url", "") if tabs else ""


def page_key(url) -> str:
    """Identity of a page for the breaker: a single-page app's route counts as a
    page of its own, the query string does not."""
    return view_key(url)


def visit_over(url_before: str, url_after: str, sig_before: str, sig_after: str, error) -> bool:
    """Is the persona on a different page after this action? A new address, or on
    the same address a page whose content moved on (a modal closed, a wizard
    step advanced) after an action that worked. A failing action that left the
    page as it was does not end the visit; neither does one that failed halfway."""
    if page_key(url_after) != page_key(url_before):
        return True
    return not error and sig_after != sig_before


class FailureBreaker:
    """Decides, before an action runs, whether retrying it is hopeless."""

    def __init__(self) -> None:
        # failure_key -> failures on the page the persona is on now.
        self.visit: Counter = Counter()
        # (page_key, failure_key) -> failures on that page, whatever happened between.
        self.total: Counter = Counter()
        # action_key -> times the audit's own rules refused exactly this action
        # (no personal data, stay on the site). Text-specific on purpose: typing
        # a different string into a field that refused one is not blocked.
        self.refusals: Counter = Counter()

    def verdict(self, action_json: str, url: str) -> Optional[str]:
        """None to let it run, else why it must not."""
        if self.refusals[action_key(action_json)] >= REFUSALS_PER_ACTION:
            return VERDICT_REFUSED
        fk = failure_key(action_json)
        if fk.startswith(_GOTO):
            return VERDICT_FAILED if self.total[("", fk)] >= FAILS_PER_VISIT else None
        if self.visit[fk] >= FAILS_PER_VISIT or self.total[(page_key(url), fk)] >= FAILS_PER_PAGE:
            return VERDICT_FAILED
        return None

    def attempts(self, action_json: str, url: str) -> int:
        """How many times this attempt has failed here, for what the persona is told."""
        fk = failure_key(action_json)
        if fk.startswith(_GOTO):
            return self.total[("", fk)]
        return max(self.visit[fk], self.total[(page_key(url), fk)])

    def record(self, action_json: str, error, url: str) -> None:
        """Count how an action ended; url is the page it was attempted on."""
        fk = failure_key(action_json)
        scope = "" if fk.startswith(_GOTO) else page_key(url)
        if not error:
            self.visit.pop(fk, None)
            self.total.pop((scope, fk), None)
        elif str(error).startswith((INPUT_REFUSAL_PREFIX, SCOPE_REFUSAL_PREFIX)):
            self.refusals[action_key(action_json)] += 1
        else:
            self.visit[fk] += 1
            self.total[(scope, fk)] += 1

    def page_changed(self) -> None:
        """The persona is on another page: its attempts there start over."""
        self.visit.clear()
