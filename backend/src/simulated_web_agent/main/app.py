# app.py — Flask API for the UXAgent dashboard.
#
# Run model is asynchronous: POST /run starts the pipeline in a background thread and
# returns a run_id immediately. The frontend polls GET /progress, then fetches results
# from GET /runs/<run_id> once status is "done".
import asyncio
import logging
import re
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import BadRequest

from . import cancellation, results_api
from .persona import generate_audit_settings, generate_example_persona
from .run import mark_run_status, new_run_id, run, site_slug

log = logging.getLogger("simulated_web_agent.main.app")

app = Flask(__name__)

PHASE_LABELS = {
    "personas": "Generating personas",
    "agents": "Running agents",
    "analysis": "Analysing sessions",
    "report": "Compiling report",
}

# In-memory run registry (no DB). Single active run is sufficient — concurrency caps
# agents within a run, not the number of runs.
RUNS: dict[str, dict] = {}
CURRENT_RUN_ID: str | None = None
_runs_lock = threading.Lock()


def _empty_progress() -> dict:
    return {
        "phase": None,
        "label": None,
        "counts": {
            "personas": {"current": 0, "total": 0},
            "agents": {"current": 0, "total": 0},
            "analysis": {"current": 0, "total": 0},
            "report": {"current": 0, "total": 0},
        },
    }


def _make_on_progress(run_id: str):
    """Build an on_progress callback that updates RUNS[run_id]['progress']."""
    def log_progress(evt: dict):
        phase = evt.get("phase")
        status = evt.get("status")
        with _runs_lock:
            entry = RUNS.get(run_id)
            if not entry:
                return
            prog = entry["progress"]
            if phase in prog["counts"]:
                if "total" in evt:
                    prog["counts"][phase]["total"] = evt["total"]
                if status == "start":
                    prog["counts"][phase]["current"] = 0
                if "current" in evt:
                    prog["counts"][phase]["current"] = evt["current"]
                prog["phase"] = phase
                prog["label"] = PHASE_LABELS.get(phase, phase.title())
        print(f"[PROGRESS] {run_id} {phase}: {evt.get('current', '')}/{evt.get('total', '')}", flush=True)

    return log_progress


def _run_thread(run_id: str, payload: dict):
    try:
        run(
            total_personas=int(payload["total_personas"]),
            demographics=payload["demographics"],
            general_intent=payload["general_intent"],
            start_url=payload["start_url"],
            max_steps=int(payload["max_steps"]),
            concurrency=int(payload.get("concurrency", 4)),
            example_persona=payload.get("example_persona", None),
            persona_slots=payload.get("persona_slots", None),
            questionnaire=payload["questionnaire"],
            headless=bool(payload.get("headless", True)),
            site_context=payload.get("site_context", ""),
            analytics_context=payload.get("analytics_context", ""),
            on_progress=_make_on_progress(run_id),
            run_id=run_id,
        )
        with _runs_lock:
            RUNS[run_id]["status"] = "done"
    except Exception as e:
        log.exception("Run %s failed: %s", run_id, e)
        with _runs_lock:
            RUNS[run_id]["status"] = "error"
            RUNS[run_id]["error"] = str(e)
            phase = (RUNS[run_id].get("progress") or {}).get("label") or ""
        try:
            err_str = str(e)
            suggestion = (
                "Retry with concurrency: 1 and headless: false, or check that the "
                "target site is reachable and the API keys in .env are valid."
            )
            mark_run_status(run_id, "error", failure={
                "phase": phase,
                "failedAt": "",
                "error": err_str[:600],
                "suggestion": suggestion,
            })
        except Exception:
            pass


@app.post("/run")
def run_endpoint():
    global CURRENT_RUN_ID
    payload = request.get_json(silent=True)
    if payload is None:
        raise BadRequest("Expected application/json body")

    required = [
        "total_personas",
        "demographics",
        "general_intent",
        "start_url",
        "max_steps",
        "questionnaire",
    ]
    missing = [k for k in required if k not in payload]
    if missing:
        raise BadRequest(f"Missing required fields: {', '.join(missing)}")

    # An empty/invalid start_url used to pass this check and then kill every
    # session on Page.goto("") — "Cannot navigate to invalid URL". Fail fast
    # instead of burning a browser fleet.
    start_url = str(payload.get("start_url") or "").strip()
    if not re.match(r"^https?://[^\s.]+\.[^\s]+", start_url, re.IGNORECASE):
        raise BadRequest(
            f"start_url must be an absolute http(s) URL, got: {start_url!r}"
        )
    if not str(payload.get("general_intent") or "").strip():
        raise BadRequest("general_intent must not be empty")

    with _runs_lock:
        # Single-active-run model: the pipeline owns a browser fleet and the
        # in-memory registry tracks one CURRENT_RUN_ID. A second concurrent
        # run starves the first and leaves the dashboard watching the wrong
        # one — reject instead (observed 2026-07-09: double /run within 47s).
        active = next(
            (rid for rid, e in RUNS.items() if e.get("status") == "running"),
            None,
        )
        if active:
            return jsonify({
                "error": "a run is already in progress",
                "active_run_id": active,
            }), 409

        run_id = new_run_id()
        RUNS[run_id] = {
            "status": "running",
            "progress": _empty_progress(),
            "error": None,
            "started_at": time.time(),
        }
        CURRENT_RUN_ID = run_id

    cancellation.clear(run_id)  # never inherit a stale cancel flag
    threading.Thread(target=_run_thread, args=(run_id, payload), daemon=True).start()

    return jsonify({"run_id": run_id, "site_slug": site_slug(payload["start_url"])}), 202


@app.get("/progress")
def progress_endpoint():
    explicit_run_id = request.args.get("run_id")
    run_id = explicit_run_id or CURRENT_RUN_ID
    with _runs_lock:
        entry = RUNS.get(run_id) if run_id else None
        # Self-heal: if no explicit run was requested and the tracked run is
        # not running while another one is, surface the running one so the
        # dashboard follows reality (e.g. after a stray run was cancelled).
        if not explicit_run_id and (not entry or entry.get("status") != "running"):
            for rid, e in RUNS.items():
                if e.get("status") == "running":
                    run_id, entry = rid, e
                    break
        if not entry:
            return jsonify({"status": "idle", "message": "No run started yet."}), 200
        prog = entry["progress"]
        phase = prog["phase"]
        status = entry["status"]
        counts = prog["counts"]
        c = counts.get(phase, {"current": 0, "total": 0}) if phase else {"current": 0, "total": 0}
        label = prog["label"] or "Initialising"
        if status == "done":
            message = "Run complete — report ready"
        elif status == "error":
            message = "Run failed"
        elif phase == "agents":
            message = f"Running agents: {c.get('current', 0)}/{c.get('total', 0)} complete"
        else:
            message = f"{label}…"
        started_at = entry.get("started_at")
        return jsonify({
            "run_id": run_id,
            "status": status,
            "phase": phase,
            "label": label,
            "current": c.get("current", 0),
            "total": c.get("total", 0),
            "message": message,
            "elapsed_seconds": round(time.time() - started_at, 1) if started_at else None,
        }), 200


@app.post("/runs/<run_id>/cancel")
def cancel_run_endpoint(run_id: str):
    """Mark a run as cancelled. The worker thread is a daemon and will finish its
    current step, but the dashboard treats the run as ended immediately."""
    with _runs_lock:
        entry = RUNS.get(run_id)
        if not entry or entry.get("status") != "running":
            return jsonify({"status": "error", "error": "no active run with that id"}), 404
        entry["status"] = "cancelled"
    # Flag it for the agent loop: sessions stop after their current step and
    # queued personas never start, instead of running on in the background.
    cancellation.cancel(run_id)
    try:
        mark_run_status(run_id, "cancelled")
    except Exception:
        pass
    return jsonify({"status": "cancelled", "run_id": run_id}), 200


@app.post("/generate-audit-settings")
def generate_audit_settings_endpoint():
    payload = request.get_json(silent=True) or {}
    start_url = payload.get("start_url", "").strip()
    if not start_url:
        return jsonify({"status": "error", "error": "start_url is required"}), 400
    try:
        settings = asyncio.run(generate_audit_settings(start_url=start_url))
        return jsonify(settings), 200
    except Exception as e:
        log.exception("Audit settings generation failed: %s", e)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.post("/generate-example-persona")
def generate_example_persona_endpoint():
    payload = request.get_json(silent=True) or {}
    try:
        persona = asyncio.run(generate_example_persona(
            general_intent=payload.get("general_intent", ""),
            start_url=payload.get("start_url", ""),
            demographics=payload.get("demographics", []),
            site_context=payload.get("site_context", ""),
        ))
        return jsonify({"persona": persona}), 200
    except Exception as e:
        log.exception("Example persona generation failed: %s", e)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.get("/runs")
def runs_list_endpoint():
    return jsonify(results_api.list_runs()), 200


@app.get("/runs/<run_id>")
def run_detail_endpoint(run_id: str):
    payload = results_api.build_run(run_id)
    if payload is None:
        return jsonify({"status": "error", "error": "run not found"}), 404
    return jsonify(payload), 200


@app.get("/runs/<run_id>/live")
def run_live_endpoint(run_id: str):
    payload = results_api.live_run(run_id)
    if payload is None:
        return jsonify({"personas": []}), 200
    return jsonify(payload), 200


@app.get("/runs/<run_id>/screenshot/<persona_id>")
def run_screenshot_endpoint(run_id: str, persona_id: str):
    shot = results_api.latest_screenshot(run_id, persona_id)
    if shot is None or not shot.exists():
        return jsonify({"status": "error", "error": "no screenshot"}), 404
    resp = send_file(shot.resolve(), mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/runs/<run_id>/screenshot/<persona_id>/<int:step>")
def run_step_screenshot_endpoint(run_id: str, persona_id: str, step: int):
    shot = results_api.step_screenshot(run_id, persona_id, step)
    if shot is None or not shot.exists():
        return jsonify({"status": "error", "error": "no screenshot for step"}), 404
    return send_file(shot.resolve(), mimetype="image/png")


@app.get("/runs/<run_id>/<persona_id>/screenshot/<filename>")
def run_evidence_file_endpoint(run_id: str, persona_id: str, filename: str):
    """Serve evidence screenshots at their run-relative path.

    Matches the relative <img> links embedded in aggregated_report.html so the
    report works identically opened from disk and served by Flask.
    """
    base = (Path("runs") / run_id / persona_id / "screenshot").resolve()
    target = (base / filename).resolve()
    if target.parent != base or not target.exists() or target.suffix != ".png":
        return jsonify({"status": "error", "error": "not found"}), 404
    return send_file(target, mimetype="image/png")


@app.get("/runs/<run_id>/report.html")
def run_report_html_endpoint(run_id: str):
    report = Path("runs") / run_id / "aggregated_report.html"
    if not report.exists():
        return jsonify({"status": "error", "error": "report not found"}), 404
    # No caching: a re-rendered report must never be masked by a stale copy in
    # the browser (the report is regenerated whenever styling or data changes).
    resp = send_file(report.resolve())
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, use_reloader=False, threaded=True)
