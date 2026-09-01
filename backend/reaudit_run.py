"""
Re-run every non-browser analysis pass on an existing run's traces, then rebuild
per-agent and aggregated reports — without re-running the browser sessions.

Covers: audit (heuristics/accessibility/first-impression + evidence),
cognitive walkthrough, objective reflection, ISO 9241-11 metrics, bias audit.
Skips viewport/link checks and the survey, which need a browser or the live
agent memory.

Use it to repair a run whose analysis chain broke (a bad LLM shape used to
cascade: one failure left a persona with no reflection/ISO/bias, and the
aggregate then silently excluded that persona entirely).

    python reaudit_run.py <run_id>          # e.g. python reaudit_run.py 2026-07-02_16-52-49_5f64
    python reaudit_run.py                   # defaults to the newest run
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from src.simulated_web_agent.agent import gpt
from src.simulated_web_agent.config import load_cfg
from src.simulated_web_agent.main.auditor import run_audit_analysis
from src.simulated_web_agent.main.bias_audit import run_bias_audit
from src.simulated_web_agent.main.cognitive_walkthrough import run_cognitive_walkthrough
from src.simulated_web_agent.main.iso_metrics import run_iso_metrics
from src.simulated_web_agent.main.reporter import (
    generate_aggregated_report,
    generate_objective_reflection,
    generate_per_agent_report,
)
from src.simulated_web_agent.main.run import read_manifest
from src.simulated_web_agent.main.trace_store import TraceStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def main() -> None:
    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)

    runs_root = Path("runs")
    if len(sys.argv) > 1:
        run_dir = runs_root / sys.argv[1]
    else:
        run_dir = max(
            (d for d in runs_root.iterdir() if d.is_dir() and (d / "run_manifest.json").exists()),
            key=lambda d: d.name,
        )
    if not run_dir.is_dir():
        print(f"ERROR: run dir not found: {run_dir}")
        return

    manifest = read_manifest(run_dir) or {}
    stores = [
        TraceStore(d) for d in sorted(run_dir.iterdir())
        if d.is_dir() and (d / "basic_info.json").exists()
    ]
    print(f"Re-auditing {run_dir.name}: {len(stores)} persona trace(s)")

    # Each stage isolated: one persona's bad artifact must not abort the rest.
    async def _stage(name: str, coro):
        try:
            await coro
        except Exception:
            log.exception("   %s failed", name)

    for s in stores:
        print(f"── {s.trace_dir.name}")
        await _stage("audit", run_audit_analysis(s))
        await _stage("cognitive walkthrough", run_cognitive_walkthrough(s))
        await _stage("per-agent report", generate_per_agent_report(s))
        await _stage("objective reflection", generate_objective_reflection(s))
        try:
            run_iso_metrics(s)
        except Exception:
            log.exception("   iso metrics failed")
        await _stage("bias audit", run_bias_audit(s))

    print("── aggregated report")
    await generate_aggregated_report(
        stores=stores,
        task_description=manifest.get("intent", ""),
        website_url=manifest.get("start_url", ""),
    )
    print(f"Done — reports regenerated under {run_dir}")


asyncio.run(main())
