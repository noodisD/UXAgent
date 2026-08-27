"""Scope findings to the site under test.

Ground truth (the diabetyk24.pl team's manual verification of run
2026-07-20_14-50-35) exposed cross-site contamination as a top source of false
report claims: one persona wandered to doz.pl — a competitor — and 6 of his 9
findings, several feeding the report's criticals, describe doz.pl pages. An
earlier run audited gemini.pl the same way. The client read them all as
diabetyk24 defects.

This gate marks any finding whose trace-resolved URL lies outside the target
site's registrable domain as `off_site`. Downstream it is excluded exactly like
`harness_suspect`: out of severity counts, aggregate grouping and the
prioritization matrix, rendered separately ("obserwacja poza badaną stroną").

Comparison uses evidence.py's trace-resolved URL (the trace wins over the LLM's
claimed url), so the gate rides on ground truth. Missing URL ⇒ fail-open
(on-site): an unprovable location must not delete a finding — the unverified
path already caps those.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def target_url_for(store) -> str:
    """The site under test for one persona store. Read from the run-level
    manifest (trace_dir's parent) rather than importing run.py (which would
    cycle back through analysis_runner). Falls back to the session's first
    observed URL — the agent always starts on the target site, so step 0 is a
    truthful anchor for pre-manifest runs. '' when neither is available.

    Single definition shared by auditor (finding scoping), session_goal_state
    (rung scoping) and iso_metrics (outcome scoping).
    """
    try:
        manifest = json.loads(
            (store.trace_dir.parent / "run_manifest.json").read_text(encoding="utf-8")
        )
        url = manifest.get("start_url") or ""
        if url:
            return url
    except Exception:
        pass
    try:
        return store.url_at_step(0)
    except Exception:
        return ""


def registrable_domain(url) -> str:
    """'https://signin.dm.pl/x' -> 'dm.pl'. Naive last-two-labels heuristic —
    fine for the .pl/.com domains this tool audits; not a public-suffix parser
    (co.uk-style TLDs would need one)."""
    if not url or not isinstance(url, str):
        return ""
    try:
        netloc = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""
    if not netloc or "." not in netloc:
        return ""
    return ".".join(netloc.split(".")[-2:])


def same_site(url, target_url) -> bool:
    """Is `url` on the same registrable domain as the site under test?

    Fails open: an empty/unparseable url or target returns True — we exclude
    only what is *provably* off-site.
    """
    target = registrable_domain(target_url)
    if not target:
        return True
    found = registrable_domain(url)
    if not found:
        return True
    return found == target


def flag_off_site(findings: list, target_url: str) -> list:
    """Stamp each finding with `off_site` (idempotent: writes both outcomes so
    re-running after a rule change clears stale flags — same contract as
    attribution.flag_harness_suspects). Returns the same list, mutated."""
    flagged = 0
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        f["off_site"] = not same_site(f.get("url"), target_url)
        flagged += bool(f["off_site"])
    if flagged:
        log.info("[site_scope] %d finding(s) flagged off-site (target %s)",
                 flagged, registrable_domain(target_url))
    return findings
