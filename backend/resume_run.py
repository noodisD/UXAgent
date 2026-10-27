"""
Finish a run whose browser sessions completed but whose analysis or report did
not — without re-driving a single browser.

    uv run python resume_run.py runs/2026-09-02_07-07-43_c98a
    uv run python resume_run.py                # newest run under runs/

The agent loop is the expensive half of an audit: dozens of LLM calls per
persona, tens of minutes of wall clock. The analysis and report phases that
follow are a handful of calls. When a run dies in between — the process is
killed, the container restarts, Cloud Run scales the instance away — all of that
browser work is already durable on disk, and re-running the whole audit to
recover the last few minutes is pure waste.

This reads what is on disk, runs only the passes whose artifacts are missing,
generates the aggregated report, and settles the manifest.

Safe to re-run: every step is skipped when its output already exists.

(Replaces the old rerender_report.py, which had three May 2026 run directories
hardcoded in a TRACE_DIRS list and had to be edited before every use.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from simulated_web_agent.agent import gpt
from simulated_web_agent.config import load_cfg
from simulated_web_agent.main.bias_audit import run_bias_audit
from simulated_web_agent.main.iso_metrics import run_iso_metrics
from simulated_web_agent.main.reporter import (
    generate_aggregated_report,
    generate_objective_reflection,
    generate_per_agent_report,
)
from simulated_web_agent.main.sus import questions_from_manifest
from simulated_web_agent.main.trace_store import TraceStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("resume_run")


def _persona_dirs(run_dir: Path) -> list[Path]:
    """Persona directories, in the order they were created."""
    return sorted(p for p in run_dir.iterdir() if p.is_dir() and (p / "basic_info.json").exists())


async def _complete_persona(store: TraceStore, q_meta: list | None = None) -> None:
    """Run only the per-agent passes whose artifacts are missing.

    Mirrors the tail of analysis_runner.run_all_analyses, and is isolated the
    same way: one failing pass must not cost the persona the rest.
    """
    d = store.trace_dir
    steps = [
        ("ux_report.json", "per-agent report", lambda: generate_per_agent_report(store)),
        ("objective_reflection.json", "objective reflection", lambda: generate_objective_reflection(store)),
        ("iso_metrics.json", "ISO metrics", lambda: run_iso_metrics(store, q_meta)),
        ("bias_audit.json", "bias audit", lambda: run_bias_audit(store)),
    ]
    for artifact, label, call in steps:
        if (d / artifact).exists():
            log.info("  [skip] %s — %s already present", label, artifact)
            continue
        log.info("  [run ] %s", label)
        try:
            result = call()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("  [fail] %s for %s", label, d.name)


async def resume(run_dir: Path) -> None:
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)

    persona_dirs = _persona_dirs(run_dir)
    if not persona_dirs:
        log.error("No persona directories with basic_info.json under %s", run_dir)
        return
    log.info("Resuming %s — %d personas", run_dir.name, len(persona_dirs))

    stores = [TraceStore(p) for p in persona_dirs]
    for store in stores:
        log.info("Persona %s", store.trace_dir.name)
        await _complete_persona(store, questions_from_manifest(manifest) or None)

    report_path = run_dir / "aggregated_report.json"
    if report_path.exists():
        log.info("Aggregated report already present — leaving it alone")
    else:
        log.info("Generating aggregated report…")
        await generate_aggregated_report(
            stores=stores,
            task_description=manifest.get("intent", ""),
            website_url=manifest.get("start_url", ""),
            site_context=manifest.get("site_context", "") or "",
            analytics_context=manifest.get("analytics_context", "") or "",
        )

    # A run whose process died keeps status "running" forever: nothing was left
    # alive to write the end state, and the in-memory registry went with it.
    if manifest.get("status") != "done":
        ended = datetime.now(timezone.utc)
        started = manifest.get("started_at")
        manifest["status"] = "done"
        manifest["ended_at"] = ended.isoformat()
        if started and not manifest.get("duration_seconds"):
            manifest["duration_seconds"] = round(
                (ended - datetime.fromisoformat(started)).total_seconds(), 1
            )
        manifest["resumed"] = True  # this run was finished offline, not in one pass
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Manifest settled: status=done")

    for name in ("aggregated_report.json", "aggregated_report.html"):
        p = run_dir / name
        log.info("  %s %s", "OK  " if p.exists() else "MISS", name)


def main() -> None:
    runs = Path("runs")
    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1])
    else:
        candidates = [p for p in runs.iterdir() if (p / "run_manifest.json").exists()]
        if not candidates:
            log.error("No runs found under %s", runs.resolve())
            sys.exit(1)
        run_dir = max(candidates, key=lambda p: p.stat().st_mtime)
        log.info("No run given; using the newest: %s", run_dir)

    if not (run_dir / "run_manifest.json").exists():
        log.error("%s has no run_manifest.json", run_dir)
        sys.exit(1)
    asyncio.run(resume(run_dir))


if __name__ == "__main__":
    main()
