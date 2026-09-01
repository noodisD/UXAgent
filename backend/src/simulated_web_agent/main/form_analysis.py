"""
Form funnel analyzer — post-hoc analysis of action_trace.json.

Classifies each agent session by form engagement and completion without
modifying the executor.  Reads the already-saved action_trace.json and
classifies actions:

  - "type" action  → user filled a form field
  - "click" on a submit-like element → user attempted to submit

The classification is heuristic: a session that filled ≥1 field and then
clicked a submit-like element is considered "submitted".

Saves:  trace_dir/form_funnel.json
"""

from __future__ import annotations

import json
import logging

from .trace_store import TraceStore

log = logging.getLogger(__name__)

# Keywords (lower-cased) that suggest a submit/CTA click
_SUBMIT_PATTERNS = [
    "submit",
    "wyślij",
    "dalej",
    "next",
    "continue",
    "kontynuuj",
    "kup",
    "zamów",
    "order",
    "szukaj",
    "search",
    "znajdź",
    "find",
    "zaloguj",
    "login",
    "zarejestruj",
    "register",
    "potwierdź",
    "confirm",
    "zapisz",
    "save",
]


def run_form_analysis(store: TraceStore) -> dict:
    """
    Analyze form interactions from the saved action_trace.json.

    Returns and saves a summary dict.
    """
    raw_actions: list = store.read_action_trace()

    typed_fields: list[dict] = []
    submit_clicks: list[dict] = []

    for raw in raw_actions:
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
        action_type = (
            action.get("action_type") or action.get("action") or ""
        ).lower()
        element_str = str(
            action.get("element") or action.get("target") or ""
        ).lower()
        value = action.get("value") or action.get("text") or ""

        if action_type == "type":
            typed_fields.append(
                {
                    "element": element_str,
                    "value_length": len(str(value)),
                }
            )

        elif action_type == "click":
            if any(pat in element_str for pat in _SUBMIT_PATTERNS):
                submit_clicks.append({"element": element_str})

    engaged = len(typed_fields) > 0
    # Submitted = filled at least one field AND clicked something submit-like
    submitted = engaged and len(submit_clicks) > 0

    result: dict = {
        "engaged_form": engaged,
        "submitted_form": submitted,
        "fields_filled": len(typed_fields),
        "submit_clicks": len(submit_clicks),
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
