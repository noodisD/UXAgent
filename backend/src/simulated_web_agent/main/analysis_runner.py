"""
AnalysisRunner — single entry point for the per-agent analysis phase.

Runs all analysis passes in parallel for one TraceStore, then generates
the per-agent report.  Callers (run.py) hold a semaphore to cap concurrency
across agents; this module has no concurrency logic.

Analysis passes
───────────────
Async (I/O-bound, run concurrently):
  • survey            — LLM-powered questionnaire
  • audit             — heuristic evaluation
  • viewport_scan     — Playwright mobile/tablet check
  • link_checker      — Playwright broken-link check

Sync/cheap (run after async passes, in series to avoid overhead):
  • scroll_depth      — aggregates already-written scroll_top_*.txt files
  • form_analysis     — classifies action_trace.json for form engagement
"""

import asyncio
import logging
from typing import Any

from .auditor import run_audit_analysis
from .bias_audit import run_bias_audit
from .cognitive_walkthrough import run_cognitive_walkthrough
from .form_analysis import run_form_analysis
from .iso_metrics import run_iso_metrics
from .link_checker import run_link_check
from .reporter import generate_objective_reflection, generate_per_agent_report
from .scroll_depth import run_scroll_analysis
from .survey import run_survey
from .trace_store import TraceStore
from .viewport_scan import run_viewport_scan

log = logging.getLogger(__name__)


async def run_all_analyses(
    store: TraceStore,
    questionnaire: dict[str, Any],
    headless: bool = True,
) -> dict:
    """
    Run all per-agent analyses then build per-agent report.

    Returns the per-agent report dict.
    """
    log.info("[analysis_runner] Starting analyses for %s", store.trace_dir)

    # I/O-bound passes run in parallel. cognitive_walkthrough reads the
    # already-written observation/action trace, so it can run alongside.
    await asyncio.gather(
        run_survey(store, questionnaire),
        run_audit_analysis(store),
        run_viewport_scan(store, headless=headless),
        run_link_check(store, headless=headless),
        run_cognitive_walkthrough(store),
    )

    # Cheap post-hoc passes (read files already written by the agent loop)
    run_scroll_analysis(store)
    run_form_analysis(store)

    # Each stage is isolated: a malformed artifact from one pass (e.g. an LLM
    # returning the wrong JSON shape) must not skip every later pass for this
    # persona — that silently cost one persona its ISO metrics and bias audit
    # in run 2026-07-16_13-41-14.
    report = {}
    try:
        report = await generate_per_agent_report(store)
    except Exception:
        log.exception("[analysis_runner] per-agent report failed for %s", store.trace_dir)
    try:
        await generate_objective_reflection(store)
    except Exception:
        log.exception("[analysis_runner] objective reflection failed for %s", store.trace_dir)

    # Post-reflection passes: ISO metrics (sync, reads reflection) and the
    # bias audit (LLM, reads findings + reflection rationale).
    try:
        run_iso_metrics(store)
    except Exception:
        log.exception("[analysis_runner] iso metrics failed for %s", store.trace_dir)
    try:
        await run_bias_audit(store)
    except Exception:
        log.exception("[analysis_runner] bias audit failed for %s", store.trace_dir)

    log.info("[analysis_runner] Done for %s", store.trace_dir)
    return report
