"""
AnalysisRunner — single entry point for the per-agent analysis phase.

Runs survey, audit, and viewport scan in parallel for one TraceStore,
then generates the per-agent report.  Callers (run.py) hold a semaphore
to cap concurrency across agents; this module has no concurrency logic.
"""

import asyncio
import logging
from typing import Any

from .auditor import run_audit_analysis
from .reporter import generate_per_agent_report
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
    Run survey + audit + viewport scan in parallel, then build per-agent report.

    Returns the per-agent report dict.
    """
    log.info("[analysis_runner] Starting analyses for %s", store.trace_dir)

    await asyncio.gather(
        run_survey(store, questionnaire),
        run_audit_analysis(store),
        run_viewport_scan(store, headless=headless),
    )

    report = await generate_per_agent_report(store)
    log.info("[analysis_runner] Done for %s", store.trace_dir)
    return report
