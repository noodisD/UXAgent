"""
Re-run link checker + form analysis on existing traces, then re-render the report.

Use this to retrofit broken-link and form-funnel data onto traces collected
before those modules existed — without re-running the full browser session.

    python reanalyze_report.py

Trace dirs are auto-detected from runs/ (all subdirectories that contain
basic_info.json).  You can override by editing TRACE_DIRS below.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml

from simulated_web_agent.agent import gpt
from simulated_web_agent.config import load_cfg
from simulated_web_agent.main.form_analysis import run_form_analysis
from simulated_web_agent.main.link_checker import run_link_check
from simulated_web_agent.main.reporter import generate_aggregated_report
from simulated_web_agent.main.trace_store import TraceStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Override here if you want specific dirs, otherwise leave empty ────────────
TRACE_DIRS: list[Path] = []
# Example:
# TRACE_DIRS = [
#     Path("runs/2026-05-15_16-08-04_80b3"),
#     Path("runs/2026-05-15_16-17-58_3555"),
# ]

HEADLESS = True   # set False to watch the browser during link checks


def _discover_stores(runs_dir: Path) -> list[TraceStore]:
    """Find all trace sub-dirs that look like completed agent runs."""
    stores = []
    for d in sorted(runs_dir.iterdir()):
        if d.is_dir() and (d / "basic_info.json").exists():
            stores.append(TraceStore(d))
    return stores


async def _reanalyze_one(store: TraceStore) -> None:
    log.info("── Analyzing %s ──", store.trace_dir.name)

    # Form funnel is synchronous and instant
    form = run_form_analysis(store)
    log.info("  form: engaged=%s submitted=%s fields=%d",
             form["engaged_form"], form["submitted_form"], form["fields_filled"])

    # Link checker needs Playwright — skip gracefully if something goes wrong
    try:
        findings = await run_link_check(store, headless=HEADLESS)
        total = sum(f.get("links_checked", 0) for f in findings)
        broken = sum(len(f.get("broken") or []) for f in findings)
        log.info("  links: %d checked, %d broken", total, broken)
    except Exception as exc:
        log.warning("  link check failed (non-fatal): %s", exc)


async def main() -> None:
    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)

    with open("conf/runConfig.yaml", "r", encoding="utf-8") as f:
        run_cfg = yaml.safe_load(f)

    runs_dir = Path("runs")

    # Resolve stores
    if TRACE_DIRS:
        stores = [TraceStore(d) for d in TRACE_DIRS if d.exists()]
    else:
        stores = _discover_stores(runs_dir)

    if not stores:
        print("ERROR: No trace directories found under runs/. "
              "Set TRACE_DIRS manually or run a study first.")
        return

    print(f"\nFound {len(stores)} trace(s):")
    for s in stores:
        print(f"  {s.trace_dir}")

    # ── Step 1: re-run link checker + form analysis on every store ────────────
    print(f"\nStep 1/2 — Running link checker + form analysis ({len(stores)} sessions)…")
    await asyncio.gather(*[_reanalyze_one(s) for s in stores])

    # ── Step 2: re-render aggregated report ───────────────────────────────────
    print("\nStep 2/2 — Regenerating aggregated report…")
    agg = await generate_aggregated_report(
        stores=stores,
        task_description=run_cfg["general_intent"],
        website_url=run_cfg["start_url"],
        site_context=run_cfg.get("site_context", ""),
        analytics_context=run_cfg.get("analytics_context", ""),
    )

    print(f"\nDone. Reports written to {runs_dir}:")
    for name in ("aggregated_report.json", "aggregated_report.md", "aggregated_report.html"):
        p = runs_dir / name
        if p.exists():
            print(f"  {p}  ({p.stat().st_size:,} bytes)")


asyncio.run(main())
