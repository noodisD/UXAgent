import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from ..agent import gpt
from ..config import load_cfg
from .analysis_runner import run_all_analyses
from .experiment import experiment_async
from .persona import generate_personas
from .reporter import generate_aggregated_report
from .trace_store import TraceStore

log = logging.getLogger("simulated_web_agent.main.run")
logging.basicConfig(level=logging.INFO)


def load_config(config_file: str) -> dict[str, Any]:
    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _safe_ping(cb: Optional[Callable[[dict], None]], evt: dict):
    if not cb:
        return
    try:
        cb(evt)
    except Exception:
        pass


async def run_async(
    total_personas: int,
    demographics: list[dict[str, Any]],
    general_intent: str,
    start_url: str,
    max_steps: int,
    questionnaire: dict,
    headless: bool = False,
    concurrency: int = 4,
    example_persona: str = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    site_context: str = "",
):
    ping = lambda e: _safe_ping(on_progress, e)

    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)

    # ── 1) Personas ──────────────────────────────────────────────────────────
    ping({"phase": "personas", "status": "start", "total": total_personas})
    all_personas_intents = await generate_personas(
        demographics=demographics,
        general_intent=general_intent,
        n=total_personas,
        on_progress=lambda k, n: ping(
            {"phase": "personas", "status": "progress", "current": k, "total": n}
        ),
        example_text=example_persona,
    )
    ping({"phase": "personas", "status": "progress", "current": total_personas, "total": total_personas})

    if site_context:
        for p in all_personas_intents:
            p["site_context"] = site_context

    with open("personas.json", "w", encoding="utf-8") as f:
        json.dump(all_personas_intents, f, indent=2, ensure_ascii=False)

    # ── 2) Experiments ────────────────────────────────────────────────────────
    total_agents = len(all_personas_intents)
    ping({"phase": "agents", "status": "start", "total": total_agents})

    trace_dirs_raw = await experiment_async(
        agents=all_personas_intents,
        start_url=start_url,
        max_steps=max_steps,
        config_name="base",
        config_path=".",
        concurrency=concurrency,
        headless=headless,
        on_progress=lambda k, n: ping(
            {"phase": "agents", "status": "progress", "current": k, "total": n}
        ),
    )
    ping({"phase": "agents", "status": "progress", "current": total_agents, "total": total_agents})

    # Filter out failed runs (exceptions returned by gather)
    stores: list[TraceStore] = [
        TraceStore(Path(td))
        for td in trace_dirs_raw
        if isinstance(td, (Path, str)) and td
        and not isinstance(td, Exception)
    ]

    # ── 3) Per-agent: survey + audit + viewport scan (parallel across agents) ─
    total_post = len(stores)
    ping({"phase": "analysis", "status": "start", "total": total_post})

    post_sem = asyncio.Semaphore(min(concurrency, 4))
    done_post = 0
    lock = asyncio.Lock()

    async def post_one(store: TraceStore):
        nonlocal done_post
        async with post_sem:
            await run_all_analyses(store, questionnaire, headless=headless)
        async with lock:
            done_post += 1
            ping({"phase": "analysis", "status": "progress", "current": done_post, "total": total_post})

    await asyncio.gather(*[post_one(s) for s in stores], return_exceptions=True)
    ping({"phase": "analysis", "status": "progress", "current": total_post, "total": total_post})

    # ── 4) Aggregated report ──────────────────────────────────────────────────
    if stores:
        ping({"phase": "report", "status": "start", "total": 1})
        try:
            await generate_aggregated_report(
                stores=stores,
                task_description=general_intent,
                website_url=start_url,
                site_context=site_context,
            )
        except Exception as e:
            log.exception("Aggregated report generation failed: %s", e)
        ping({"phase": "report", "status": "progress", "current": 1, "total": 1})


def run(*args, **kwargs):
    asyncio.run(run_async(*args, **kwargs))


if __name__ == "__main__":
    cfg_dict = load_config("conf/runConfig.yaml")
    asyncio.run(
        run_async(
            total_personas=cfg_dict.get("total_personas", 8),
            demographics=cfg_dict["demographics"],
            general_intent=cfg_dict["general_intent"],
            start_url=cfg_dict["start_url"],
            max_steps=cfg_dict["max_steps"],
            questionnaire=cfg_dict["questionnaire"],
            example_persona=cfg_dict["example_persona"],
            concurrency=cfg_dict["concurrency"],
            headless=True,
            site_context=cfg_dict.get("site_context", ""),
        )
    )
