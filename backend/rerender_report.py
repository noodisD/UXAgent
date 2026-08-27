"""
One-shot script to re-render the aggregated report from existing trace data.
Run from the UXAgent root:  python rerender_report.py
"""
import asyncio
import logging
from pathlib import Path

import yaml

from src.simulated_web_agent.agent import gpt
from src.simulated_web_agent.config import load_cfg
from src.simulated_web_agent.main.reporter import generate_aggregated_report
from src.simulated_web_agent.main.trace_store import TraceStore

logging.basicConfig(level=logging.INFO)

TRACE_DIRS = [
    Path("runs/2026-05-15_16-08-04_80b3"),
    Path("runs/2026-05-15_16-17-58_3555"),
    Path("runs/2026-05-15_16-26-02_839a"),
]


async def main():
    # Load provider config
    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)

    # Load run config for task description, URL, and site_context
    with open("conf/runConfig.yaml", "r", encoding="utf-8") as f:
        run_cfg = yaml.safe_load(f)

    stores = [TraceStore(d) for d in TRACE_DIRS if d.exists()]
    if not stores:
        print("ERROR: No trace directories found.")
        return

    print(f"Re-rendering report from {len(stores)} trace(s): {[str(s.trace_dir) for s in stores]}")

    agg = await generate_aggregated_report(
        stores=stores,
        task_description=run_cfg["general_intent"],
        website_url=run_cfg["start_url"],
        site_context=run_cfg.get("site_context", ""),
    )

    runs_dir = stores[0].trace_dir.parent
    print(f"\nDone. Reports written to {runs_dir}:")
    for name in ("aggregated_report.json", "aggregated_report.md", "aggregated_report.html"):
        p = runs_dir / name
        if p.exists():
            print(f"  {p}  ({p.stat().st_size:,} bytes)")


asyncio.run(main())
