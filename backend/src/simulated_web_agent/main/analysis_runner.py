"""
AnalysisRunner — single entry point for the per-agent analysis phase.

Runs all analysis passes in parallel for one TraceStore, then generates
the per-agent report.  Callers (run.py) hold a semaphore to cap concurrency
across agents; this module has no concurrency logic.

Analysis passes
───────────────
Async (I/O-bound, run concurrently):
  • survey                — LLM-powered questionnaire
  • audit                 — heuristic evaluation
  • viewport_scan         — Playwright mobile/tablet/desktop check
  • link_checker          — Playwright broken-link check
  • cognitive_walkthrough — reads the already-written observation/action trace

Sync/cheap (run after async passes, in series to avoid overhead):
  • scroll_depth      — aggregates already-written scroll_top_*.txt files
  • form_analysis     — classifies action_trace.json for form engagement

Then, in order (each depends on what the previous wrote):
  • per-agent report      — ux_report.{json,md}
  • objective_reflection  — structured outcome/blockers/wins
  • iso_metrics           — sync, reads the reflection
  • bias_audit            — LLM, reads findings + reflection rationale

To add a pass: put it in the group that matches its cost and dependencies, and
keep it isolated — every stage below the parallel block is individually
try/except'd, and the parallel block uses return_exceptions=True, so one
failing pass never costs the persona the rest of its analysis.
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
    #
    # return_exceptions=True is what makes the isolation described below apply
    # to these five too. Without it one raise here aborted the whole coroutine,
    # so the persona never reached generate_per_agent_report and had no
    # ux_report.json — reporter.py then dropped it from the aggregate while
    # results_api still listed it, leaving participant_count disagreeing with
    # the dashboard's persona count and nothing marking the discrepancy.
    parallel = {
        "survey": run_survey(store, questionnaire),
        "audit": run_audit_analysis(store),
        "viewport_scan": run_viewport_scan(store, headless=headless),
        "link_check": run_link_check(store, headless=headless),
        "cognitive_walkthrough": run_cognitive_walkthrough(store),
    }
    results = await asyncio.gather(*parallel.values(), return_exceptions=True)
    for name, result in zip(parallel.keys(), results):
        if isinstance(result, BaseException):
            log.exception(
                "[analysis_runner] %s failed for %s", name, store.trace_dir,
                exc_info=result,
            )

    # Cheap post-hoc passes (read files already written by the agent loop)
    try:
        run_scroll_analysis(store)
    except Exception:
        log.exception("[analysis_runner] scroll analysis failed for %s", store.trace_dir)
    try:
        run_form_analysis(store)
    except Exception:
        log.exception("[analysis_runner] form analysis failed for %s", store.trace_dir)

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
