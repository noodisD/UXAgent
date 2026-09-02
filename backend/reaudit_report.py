"""
Re-run heuristic/audit evaluation on existing traces (no browser),
then re-render the full aggregated report.

Run from the UXAgent root:  python reaudit_report.py
"""
import asyncio
import json
import logging
from pathlib import Path

import yaml

from simulated_web_agent.agent import gpt
from simulated_web_agent.config import load_cfg
from simulated_web_agent.main.auditor import run_audit_analysis
from simulated_web_agent.main.reporter import generate_aggregated_report, generate_per_agent_report
from simulated_web_agent.main.trace_store import TraceStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

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

    site_context = run_cfg.get("site_context", "")
    task_description = run_cfg["general_intent"]
    start_url = run_cfg["start_url"]

    stores = [TraceStore(d) for d in TRACE_DIRS if d.exists()]
    if not stores:
        print("ERROR: No trace directories found.")
        return

    # Step 1 — patch site_context into each trace's basic_info.json
    print("Patching site_context into basic_info.json for each trace...")
    for store in stores:
        bi = store.read_basic_info()
        bi["site_context"] = site_context
        (store.trace_dir / "basic_info.json").write_text(
            json.dumps(bi, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("  Done.\n")

    # Step 2 — re-run audit analysis (no browser; reads memory/a11y/screenshots from trace)
    print(f"Re-auditing {len(stores)} traces with site_context-aware prompt...")
    for store in stores:
        print(f"  Auditing {store.trace_dir.name} ...")
        await run_audit_analysis(store)
    print("  Done.\n")

    # Step 3 — regenerate per-agent reports
    print("Regenerating per-agent reports...")
    for store in stores:
        await generate_per_agent_report(store)
    print("  Done.\n")

    # Step 4 — regenerate aggregated report
    print("Regenerating aggregated report...")
    agg = await generate_aggregated_report(
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
