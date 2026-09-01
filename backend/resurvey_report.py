"""
Re-run survey only on existing traces (no browser, no re-audit),
then re-render per-agent and aggregated reports.

Run from the UXAgent root:  python resurvey_report.py
"""
import asyncio
import logging
from pathlib import Path

import yaml

from src.simulated_web_agent.agent import gpt
from src.simulated_web_agent.config import load_cfg
from src.simulated_web_agent.main.reporter import generate_aggregated_report, generate_per_agent_report
from src.simulated_web_agent.main.survey import run_survey
from src.simulated_web_agent.main.trace_store import TraceStore

logging.basicConfig(level=logging.INFO)

TRACE_DIRS = [
    Path("runs/2026-05-15_16-08-04_80b3"),
    Path("runs/2026-05-15_16-17-58_3555"),
    Path("runs/2026-05-15_16-26-02_839a"),
]


async def main():
    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)

    with open("conf/runConfig.yaml", "r", encoding="utf-8") as f:
        run_cfg = yaml.safe_load(f)

    questionnaire = run_cfg["questionnaire"]
    task_description = run_cfg["general_intent"]
    start_url = run_cfg["start_url"]
    site_context = run_cfg.get("site_context", "")

    stores = [TraceStore(d) for d in TRACE_DIRS if d.exists()]
    if not stores:
        print("ERROR: No trace directories found.")
        return

    # Step 1 — re-run survey with updated questionnaire + site_context
    print(f"Re-running survey for {len(stores)} traces...")
    for store in stores:
        print(f"  {store.trace_dir.name} ...")
        await run_survey(store, questionnaire)
    print("  Done.\n")

    # Step 2 — regenerate per-agent reports
    print("Regenerating per-agent reports...")
    for store in stores:
        await generate_per_agent_report(store)
    print("  Done.\n")

    # Step 3 — regenerate aggregated report
    print("Regenerating aggregated report...")
    await generate_aggregated_report(
        stores=stores,
        task_description=task_description,
        website_url=start_url,
        site_context=site_context,
    )

    runs_dir = stores[0].trace_dir.parent
    print(f"\nDone. Reports written to {runs_dir}:")
    for name in ("aggregated_report.json", "aggregated_report.md", "aggregated_report.html"):
        p = runs_dir / name
        if p.exists():
            print(f"  {p}  ({p.stat().st_size:,} bytes)")


asyncio.run(main())
