"""
Evidence resolution for audit findings.

Every client-facing claim must be traceable to real proof: the step screenshot,
the exact page URL, and the agent's verbatim observation. The LLM only *cites*
a step; this module verifies the citation against the trace (the source of
truth) and stamps each finding `verified: True/False`. Findings that cannot be
grounded are kept but must be rendered with an "unverified" label.
"""
import logging

from .trace_store import TraceStore

log = logging.getLogger(__name__)

_QUOTE_CHARS = 400


def resolve_evidence(store: TraceStore, finding: dict) -> dict:
    """Enrich a finding with verified evidence from the trace.

    Adds/overwrites: step (int|None), url, screenshot (relative path or None),
    observation_quote, verified. When the trace URL disagrees with the
    LLM-claimed one, the trace wins and the claim is kept as `claimed_url`.
    """
    enriched = dict(finding)

    step = enriched.get("step")
    try:
        step = int(step)
    except (TypeError, ValueError):
        step = None

    n_steps = len(store.read_observations())
    if step is None or not 0 <= step < n_steps:
        enriched.update(
            {"step": step, "screenshot": None, "observation_quote": "", "verified": False}
        )
        return enriched

    trace_url = store.url_at_step(step)
    claimed_url = enriched.get("url") or ""
    if trace_url:
        if claimed_url and claimed_url != trace_url:
            enriched["claimed_url"] = claimed_url
        enriched["url"] = trace_url

    screenshot = None
    for full_page in (False, True):
        path = store.screenshot_path(step, full_page=full_page)
        if path.exists():
            screenshot = str(path.relative_to(store.trace_dir)).replace("\\", "/")
            break

    quote = store.read_observation_step_text(step).strip()
    if len(quote) > _QUOTE_CHARS:
        quote = quote[:_QUOTE_CHARS] + "…"

    enriched.update(
        {
            "step": step,
            "screenshot": screenshot,
            "observation_quote": quote,
            "verified": bool(screenshot or quote),
        }
    )
    return enriched


def resolve_all(store: TraceStore, findings: list) -> list:
    """resolve_evidence over a list, tolerating malformed entries."""
    out = []
    for f in findings or []:
        if isinstance(f, dict):
            try:
                out.append(resolve_evidence(store, f))
            except Exception:
                log.exception("evidence resolution failed for finding: %s", f)
                out.append({**f, "verified": False})
        else:
            out.append(f)
    return out
