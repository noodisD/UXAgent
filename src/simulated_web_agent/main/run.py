import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import yaml

from ..agent import gpt
from ..config import load_cfg
from .analysis_runner import run_all_analyses
from .experiment import experiment_async
from .pagespeed import run_pagespeed_check
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


def site_slug(url: str) -> str:
    """Derive a readable host label from a URL, e.g. https://www.t-mobile.pl/ -> t-mobile.pl."""
    host = urlparse(url).hostname or "site"
    return re.sub(r"^www\.", "", host)


def new_run_id() -> str:
    """A run-group id used as the runs/<run_id>/ folder name."""
    return f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:4]}"


def _write_manifest(run_dir: Path, data: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _run_scope() -> dict:
    """What produced this run: provider, the model behind each tier, and a
    digest of the prompts. Resolves the tiers rather than naming them, since
    "large" and "small" are the same model under gemini and a report that said
    "large" would tell the reader nothing."""
    from ..agent import gpt
    from ..agent.prompt_registry import registry

    cfg = load_cfg()
    provider = cfg.llm_provider
    models: dict[str, str] = {}
    for tier, router, suffix in (
        ("agent", gpt.chat_router, ""),
        ("perceive", gpt.chat_router, "_lite"),
        ("audit", gpt.slow_chat_router, "_report"),
    ):
        try:
            name = provider + suffix
            if not any(m["model_name"] == name for m in router.model_list):
                name = provider  # tier not configured for this provider
            models[tier] = next(
                m["litellm_params"]["model"]
                for m in router.model_list if m["model_name"] == name
            )
        except Exception:  # never let scope reporting break a run
            models[tier] = None
    return {
        "provider": provider,
        "models": models,
        "prompts_version": registry.version_hash(),
    }


def read_manifest(run_dir: Path) -> dict | None:
    path = Path(run_dir) / "run_manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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
    persona_slots: Optional[list[dict]] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    site_context: str = "",
    analytics_context: str = "",
    run_id: Optional[str] = None,
):
    ping = lambda e: _safe_ping(on_progress, e)

    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)

    # ── 0) Run-group directory + manifest ────────────────────────────────────
    if run_id is None:
        run_id = new_run_id()
    run_dir = Path("runs") / run_id
    started_at = datetime.now(timezone.utc)
    _write_manifest(
        run_dir,
        {
            "run_id": run_id,
            "site": site_slug(start_url),
            "start_url": start_url,
            "intent": general_intent,
            # What the site actually is (catalog / locator / shop...). It reaches
            # every judgement pass via each persona's basic_info, but was not
            # recorded here — so a finished run could not show, in its Scope
            # section, which site_context produced the findings, and reading the
            # manifest wrongly suggested calibration was off. Record it.
            "site_context": site_context or "",
            "max_steps": max_steps,
            "persona_count": total_personas,
            "questionnaire": questionnaire,
            # Scope (§6.1): what produced these findings. Recorded at run time
            # because it cannot be reconstructed afterwards — prompts and model
            # both move, and two runs whose scope differs are not comparable.
            # Runs made before this existed honestly report null.
            "scope": _run_scope(),
            "started_at": started_at.isoformat(),
            "ended_at": None,
            "duration_seconds": None,
            "status": "running",
        },
    )

    def _finalize(status: str) -> None:
        ended_at = datetime.now(timezone.utc)
        manifest = read_manifest(run_dir) or {}
        manifest.update(
            {
                "ended_at": ended_at.isoformat(),
                "duration_seconds": round((ended_at - started_at).total_seconds(), 1),
                "status": status,
            }
        )
        _write_manifest(run_dir, manifest)

    # ── 1) Personas ──────────────────────────────────────────────────────────
    # persona_slots (optional) lets the wizard mix manual and auto personas:
    # [{"mode": "auto"|"manual", "description": "<full text for manual>"}, ...]
    slots = list(persona_slots or [])
    if len(slots) < total_personas:
        slots += [{"mode": "auto"}] * (total_personas - len(slots))
    slots = slots[:total_personas]

    manual_count = sum(
        1 for s in slots
        if s.get("mode") == "manual" and str(s.get("description", "")).strip()
    )
    n_auto = total_personas - manual_count

    ping({"phase": "personas", "status": "start", "total": total_personas})
    generated: list[dict[str, Any]] = []
    if n_auto > 0:
        generated = await generate_personas(
            demographics=demographics,
            general_intent=general_intent,
            n=n_auto,
            on_progress=lambda k, n: ping(
                {"phase": "personas", "status": "progress",
                 "current": manual_count + k, "total": total_personas}
            ),
            example_text=example_persona,
        )

    # Merge in slot order: manual slots keep the user's text, auto slots consume
    # from the generated list.
    all_personas_intents: list[dict[str, Any]] = []
    gen_iter = iter(generated)
    for s in slots:
        desc = str(s.get("description", "")).strip()
        if s.get("mode") == "manual" and desc:
            all_personas_intents.append({"persona": desc, "intent": general_intent})
        else:
            all_personas_intents.append(next(gen_iter))
    log.info("Personas ready: %d auto, %d manual", n_auto, manual_count)
    ping({"phase": "personas", "status": "progress", "current": total_personas, "total": total_personas})

    if site_context:
        for p in all_personas_intents:
            p["site_context"] = site_context
    if analytics_context:
        for p in all_personas_intents:
            p["analytics_context"] = analytics_context

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
        runs_dir=run_dir,
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

    # ── 4) Page speed check (once, for start_url) ────────────────────────────
    if stores:
        runs_dir = stores[0].trace_dir.parent
        try:
            await run_pagespeed_check(start_url, runs_dir)
        except Exception as e:
            log.exception("Page speed check failed (non-fatal): %s", e)

    # ── 5) Aggregated report ──────────────────────────────────────────────────
    if stores:
        ping({"phase": "report", "status": "start", "total": 1})
        try:
            await generate_aggregated_report(
                stores=stores,
                task_description=general_intent,
                website_url=start_url,
                site_context=site_context,
                analytics_context=analytics_context,
            )
        except Exception as e:
            log.exception("Aggregated report generation failed: %s", e)
        ping({"phase": "report", "status": "progress", "current": 1, "total": 1})

    _finalize("done")
    return run_id


def mark_run_status(run_id: str, status: str, failure: Optional[dict] = None) -> None:
    """Update a run's manifest status out-of-band (e.g. on error from the caller).

    failure (optional): {"phase", "failedAt", "error", "suggestion"} — surfaced
    in the dashboard's failed-run modal.
    """
    run_dir = Path("runs") / run_id
    manifest = read_manifest(run_dir)
    if manifest is None:
        return
    manifest["status"] = status
    if failure:
        manifest["failure"] = failure
    if manifest.get("ended_at") is None:
        manifest["ended_at"] = datetime.now(timezone.utc).isoformat()
    _write_manifest(run_dir, manifest)


def run(*args, **kwargs):
    return asyncio.run(run_async(*args, **kwargs))


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
            analytics_context=cfg_dict.get("analytics_context", ""),
        )
    )
