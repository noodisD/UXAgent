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
from ..executor.env import WebAgentEnv
from .analysis_runner import run_all_analyses
from . import cancellation, run_storage
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


def no_evidence_failure(persona_count: int, start_url: str, reasons: list[str]) -> dict:
    """The failure payload for a run that never saw the site.

    Its own function because the wording is the point. This is what the history
    row and the failed-run modal show, and the one thing it must not do is read
    like a verdict on the site: the reviewer's t-mobile run produced "a critical
    barrier ON THE SITE" for a page that was never reached.
    """
    return {
        "phase": "sessions",
        "failedAt": start_url,
        "error": (
            f"No session observed the site: all {persona_count} personas either "
            f"ended at step 0 or spent the whole session on a bot-detection "
            f"challenge. Reported reason(s): " + "; ".join(reasons)
        ),
        "suggestion": (
            "Nothing about the site was observed, so no usability verdict is "
            "possible. Check that the site is reachable from this machine (DNS, "
            "proxy, TLS interception, geo-blocking) and that its bot protection is "
            "not challenging the browser, then re-run. "
            "This is not a finding about the site."
        ),
    }


def new_run_id() -> str:
    """A run-group id used as the runs/<run_id>/ folder name."""
    return f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:4]}"


def _write_manifest(run_dir: Path, data: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _archive(run_dir: Path) -> None:
    """Copy a finished run into durable storage, if a bucket is configured.

    Called once the manifest reaches its terminal state, so what lands in the
    bucket is a complete run. results_api is imported here, not at module
    scope, because it imports read_manifest from this module.

    Never raises: the run itself has already succeeded or failed on its own
    merits, and a storage problem must not change that verdict.
    """
    if not run_storage.is_enabled():
        return
    try:
        from .results_api import build_history_row

        run_storage.upload_run(run_dir, history_row=build_history_row(run_dir))
    except Exception:
        log.exception("[storage] archiving run %s failed", run_dir.name)


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
    # desktop | mobile | both. "both" alternates personas between the two
    # rather than doubling the session count (see experiment.devices_for_run).
    device: str = "desktop",
    on_progress: Optional[Callable[[dict], None]] = None,
    site_context: str = "",
    analytics_context: str = "",
    run_id: Optional[str] = None,
):
    ping = lambda e: _safe_ping(on_progress, e)

    # Every run() is its own asyncio.run(), so its own event loop, and runs are
    # serialised by the RUNS registry. Any shared Playwright driver still on the
    # class here belongs to a destroyed loop — awaiting it hangs rather than
    # raising. Drop it before anything can pick it up.
    WebAgentEnv.reset_shared_playwright()

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
            # Which device(s) the journeys ran on. Recorded because the
            # findings mean different things on a phone than on a desktop,
            # and a finished run could otherwise not say which it was.
            "device": device,
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

    def _finalize(status: str, failure: Optional[dict] = None) -> None:
        ended_at = datetime.now(timezone.utc)
        manifest = read_manifest(run_dir) or {}
        manifest.update(
            {
                "ended_at": ended_at.isoformat(),
                "duration_seconds": round((ended_at - started_at).total_seconds(), 1),
                "status": status,
            }
        )
        # A run that finished has no failure. Something else may have written
        # one onto this manifest while the run was still going — the orphan
        # reaper does exactly that when another process imports app.py — and
        # leaving it behind produces a manifest that says "done" and carries
        # "the server restarted while this audit was running", which the
        # dashboard renders as a failed-run modal over a successful audit.
        manifest.pop("failure", None)
        if failure:
            # Same shape mark_run_status writes, so the dashboard's failed-run
            # modal reads both paths identically.
            manifest["failure"] = failure
        _write_manifest(run_dir, manifest)
        _archive(run_dir)

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
        device=device,
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

    if cancellation.is_cancelled(run_id):
        log.info("Run %s cancelled — skipping analysis and report phases", run_id)
        _finalize("cancelled")
        return run_id

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
    #
    # A session that never took a step observed nothing: the page did not load,
    # so there is no screenshot, no action, no page text — nothing any judgement
    # could rest on. If that is true of every persona, the run has no evidence
    # at all and must not produce a score.
    #
    # Found by re-running the review cases: t-mobile.pl was unreachable (the
    # certificate was intercepted on the way out), both sessions ended at step
    # zero, and the run still finished "done" with a report reading "a critical
    # barrier ON THE SITE" and 1.0/5 — a confident verdict about a page nobody
    # ever saw, recommending the site fix a fault that was never theirs. The
    # older guard below could not catch it: stores existed and a report was
    # written, so nothing looked missing.
    # A session spent entirely behind a bot-detection challenge is the same
    # situation wearing different clothes: the page loaded and rendered, so
    # steps were taken, but every one of them was the challenge screen rather
    # than the site. hebe.pl did this in the reviewer's run and produced six
    # critical "site" defects out of it, one advising the site to put a timeout
    # on its own bot protection.
    from . import attribution as _attr

    def _observed_the_site(store) -> bool:
        if int(((store.read_session_result() or {}).get("steps_taken") or 0)) <= 0:
            return False
        try:
            return not _attr.session_blocked_by_bot_wall(store)
        except Exception:
            return True

    observed = [s for s in stores if _observed_the_site(s)]
    if stores and not observed:
        reasons = sorted({
            str((s.read_session_result() or {}).get("error")
                or ("blocked by bot-detection challenge"
                    if _attr.session_blocked_by_bot_wall(s) else "unknown"))[:200]
            for s in stores
        })
        _finalize("error", failure=no_evidence_failure(len(stores), start_url, reasons))
        return run_id

    report_error: Exception | None = None
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
            report_error = e
        ping({"phase": "report", "status": "progress", "current": 1, "total": 1})

    # The report is the deliverable, so verify the artifact exists rather than
    # trusting that nothing raised. Two silent paths reach here with no report
    # and no exception: every persona failed (so `stores` is empty and the block
    # above was skipped entirely), or generate_aggregated_report returned {}
    # because no per-agent report survived — analysis_runner deliberately
    # swallows per-persona failures, so that is reachable without anything
    # raising. Both used to finalise "done", which the history then rendered as
    # a completed audit scoring 0.0: indistinguishable from "we audited the site
    # and found nothing".
    if report_error is None and not (run_dir / "aggregated_report.json").is_file():
        _finalize("error", failure={
            "phase": "report",
            "failedAt": "",
            "error": (
                f"No report was produced: {len(stores)} of {total_personas} "
                f"personas finished with usable analysis."
            ),
            "suggestion": (
                "The sessions themselves may still be on disk — inspect the run "
                "folder before repeating the audit. The usual causes are every "
                "persona failing to start (check the site is reachable and is "
                "not blocking automation) and LLM calls failing run-wide (check "
                "provider credentials and quota)."
            ),
        })
        return run_id

    if report_error is not None:
        # The report is the deliverable. Finalising "done" after it failed
        # showed the run as complete with score 0.0 in the history, which reads
        # as "we audited the site and found nothing" rather than "we produced
        # nothing" — the two are indistinguishable to the user.
        _finalize("error", failure={
            "phase": "report",
            "failedAt": "",
            "error": str(report_error)[:600],
            "suggestion": (
                "Sessions and per-persona analyses completed; only the aggregated "
                "report failed. Re-run the report over the existing trace rather "
                "than repeating the whole audit."
            ),
        })
        return run_id

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
    # A crashed or cancelled run belongs in the durable history too — that is
    # where the failed-run modal gets its diagnostics from after a restart.
    _archive(run_dir)


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
