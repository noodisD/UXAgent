"""
Form funnel analyzer — post-hoc analysis of action_trace.json.

Classifies each agent session by form engagement and completion without
modifying the executor. Reads the saved action trace and, for each click, the
simplified page it was made on:

  - "type" action → the participant filled a field (engaged)
  - a click that actually submits a form, made after typing → submitted

"Actually submits" is decided by HTML semantics, not by the control's name. It
used to be a substring match of the semantic id against words like "zapisz",
"dalej", "kup" and "szukaj", with no check of order or element. The hyundai run
reported "Dotarli do akcji submit 1/3" for a persona who clicked the *link*
"Zapisz się na jazdę testową" — an <a>, outside any form — three times, before
her only typing, which was a dealer-city search. No form was submitted by
anyone.

Now a submission is a click, after at least one typed field, on an <input
type=submit|image>, a <button type=submit>, or a <button> inside a <form> with
no type (HTML's default is submit). Search forms are counted separately: a
search box is not a conversion form.

Saves:  trace_dir/form_funnel.json
"""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser

from .trace_store import TraceStore

log = logging.getLogger(__name__)

# A submit control whose label or form says it is a search.
_SEARCH_RE = re.compile(r"(szuka|wyszuk|search|znajd|find)", re.IGNORECASE)


class _ElementLocator(HTMLParser):
    """Find one element by parser-semantic-id and note its form context."""

    def __init__(self, semantic_id: str):
        super().__init__(convert_charrefs=True)
        self.semantic_id = semantic_id
        self.found: dict | None = None
        self._form_stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._form_stack.append({"attrs": a, "has_search_input": False})
        elif tag == "input" and self._form_stack and a.get("type", "").lower() == "search":
            self._form_stack[-1]["has_search_input"] = True
        if self.found is None and a.get("parser-semantic-id") == self.semantic_id:
            form = self._form_stack[-1] if self._form_stack else None
            self.found = {"tag": tag, "attrs": a, "form": form}

    def handle_endtag(self, tag):
        if tag == "form" and self._form_stack:
            self._form_stack.pop()


def _locate(html: str, semantic_id: str) -> dict | None:
    parser = _ElementLocator(semantic_id)
    try:
        parser.feed(html)
    except Exception:
        return parser.found
    return parser.found


def classify_click(element: dict | None) -> str | None:
    """"submit", "search", or None for a click that submits nothing."""
    if not element:
        return None
    tag = element.get("tag")
    attrs = element.get("attrs") or {}
    form = element.get("form")
    kind = attrs.get("type", "").lower()
    if tag == "input":
        submits = kind in ("submit", "image")
    elif tag == "button":
        submits = kind == "submit" or (form is not None and kind in ("", "submit"))
    else:
        submits = False  # links, divs, spans: navigation, not submission
    if not submits:
        return None
    label = " ".join(str(attrs.get(k, "")) for k in
                     ("aria-label", "value", "name", "id", "parser-semantic-id"))
    form_attrs = (form or {}).get("attrs") or {}
    if ((form or {}).get("has_search_input") or form_attrs.get("role") == "search"
            or _SEARCH_RE.search(label)
            or _SEARCH_RE.search(" ".join(str(v) for v in form_attrs.values()))):
        return "search"
    return "submit"


def _simp_html(store: TraceStore, step: int) -> str | None:
    path = store.trace_dir / "simp_html" / f"simp_html_{step}.html"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def run_form_analysis(store: TraceStore) -> dict:
    """Analyze form interactions from the saved action trace. Returns and saves."""
    raw_actions: list = store.read_action_trace()

    typed_fields: list[dict] = []
    submit_clicks: list[dict] = []
    search_submits = 0
    undetermined = 0

    for step, raw in enumerate(raw_actions):
        # Entries may be plain dicts OR JSON strings — normalise to dict
        if isinstance(raw, str):
            try:
                action: dict = json.loads(raw)
            except Exception:
                continue
        elif isinstance(raw, dict):
            action = raw
        else:
            continue

        # Field names differ between agent versions:
        #   new:  action_type / element
        #   old:  action / target
        action_type = (action.get("action_type") or action.get("action") or "").lower()
        target = str(action.get("element") or action.get("target") or "")
        value = action.get("value") or action.get("text") or ""

        if action_type == "type":
            typed_fields.append({"element": target.lower(), "value_length": len(str(value))})
            continue

        # A submission needs something typed first: clicking "Zapisz się" on
        # the way to a form submits nothing.
        if action_type != "click" or not typed_fields:
            continue
        html = _simp_html(store, step)
        if html is None:
            undetermined += 1
            continue
        verdict = classify_click(_locate(html, target))
        if verdict == "submit":
            submit_clicks.append({"element": target.lower(), "step": step})
        elif verdict == "search":
            search_submits += 1

    engaged = len(typed_fields) > 0
    submitted = len(submit_clicks) > 0

    result: dict = {
        "engaged_form": engaged,
        "submitted_form": submitted,
        "fields_filled": len(typed_fields),
        "submit_clicks": len(submit_clicks),
        # Search forms submitted — reported apart, a search is not a conversion.
        "search_submits": search_submits,
        # Clicks after typing on steps whose page was not recorded.
        "undetermined_clicks": undetermined,
        "typed_fields": typed_fields[:10],
        "submit_elements": submit_clicks[:5],
    }

    store.write_form_funnel(result)
    log.info(
        "[form] %s — engaged=%s submitted=%s fields=%d",
        store.trace_dir.name,
        engaged,
        submitted,
        len(typed_fields),
    )
    return result
