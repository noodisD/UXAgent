"""
results_api — pure transforms from on-disk run artifacts to dashboard payloads.

The Flask layer (app.py) stays thin: it calls these functions and JSON-encodes the
result. Everything here reads the filesystem via TraceStore + run_manifest.json and
maps it to the shapes the frontend consumes (see experiment-ui sample-data.js).

Layout produced by run.py:
    runs/<run_id>/run_manifest.json
    runs/<run_id>/aggregated_report.{json,md,html}
    runs/<run_id>/pagespeed.json
    runs/<run_id>/<persona_trace>/...   (one folder per persona)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .run import read_manifest
from .trace_store import TraceStore

RUNS_ROOT = Path("runs")

# Polish severity -> dashboard severity
_SEV_EN = {"krytyczny": "critical", "poważny": "serious", "drobny": "minor"}

# first_impression.scores keys -> dashboard firstImpression keys
_FI_MAP = {
    "purpose": "purpose_clarity",
    "navigation": "navigation_visibility",
    "cta": "call_to_action",
    "hierarchy": "visual_hierarchy",
    "trust": "trustworthiness",
}


def _read_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except Exception:
        return iso


def _fmt_duration(seconds: float | None) -> str | None:
    if not seconds:
        return None
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _persona_dirs(run_dir: Path) -> list[Path]:
    """Persona trace folders under a run dir (those containing basic_info.json)."""
    if not run_dir.is_dir():
        return []
    return sorted(
        d for d in run_dir.iterdir()
        if d.is_dir() and (d / "basic_info.json").exists()
    )


def _persona_duration(trace_dir: Path) -> str | None:
    shots = list((trace_dir / "screenshot").glob("screenshot_*.png"))
    if len(shots) < 2:
        return None
    mtimes = sorted(p.stat().st_mtime for p in shots)
    return _fmt_duration(mtimes[-1] - mtimes[0])


def _viewport_rows(viewport: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for v in viewport or []:
        device = v.get("viewport", "")
        if v.get("horizontal_overflow"):
            rows.append({"device": device, "issue": "Horizontal overflow detected", "severity": "serious"})
        for sf in v.get("small_fonts") or []:
            txt = (sf.get("text") or "").strip()
            label = f'Small font {sf.get("size")}px' + (f': "{txt[:40]}"' if txt else "")
            rows.append({"device": device, "issue": label, "severity": "minor"})
    return rows


def _survey_rows(survey: dict, q_prompts: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    for a in (survey or {}).get("answers", []) or []:
        qid = a.get("question_id", "")
        rows.append({
            "q": q_prompts.get(qid, a.get("prompt") or qid),
            "answer": str(a.get("value", a.get("answer", ""))),
            "reasoning": a.get("reason", a.get("reasoning", "")),
        })
    return rows


def build_persona(trace_dir: Path, q_prompts: dict[str, str]) -> dict:
    store = TraceStore(trace_dir)
    card = store.read_persona_card() or {}
    basic = store.read_basic_info()
    reflection = store.read_objective_reflection() or {}
    fi = store.read_first_impression() or {}
    acc = store.read_accessibility_findings() or {}
    viewport = store.read_viewport_findings() or []
    heuristics = store.read_heuristic_findings() or []
    survey = store.read_survey_result() or {}
    steps = len(store.read_action_trace() or [])

    demographics = card.get("demographics", {}) or {}
    fi_scores = fi.get("scores", {}) or {}

    return {
        "id": trace_dir.name,
        "name": card.get("name", "Unknown"),
        "objective": card.get("objective") or basic.get("intent", ""),
        "demographics": demographics,
        "metaParts": [str(v) for v in demographics.values()],
        "outcome": reflection.get("outcome", "partial"),
        "steps": steps,
        "duration": _persona_duration(trace_dir),
        "reflection": {
            "summary": reflection.get("summary", ""),
            "blockers": reflection.get("blockers", []),
            "wins": reflection.get("wins", []),
            "priorityFix": reflection.get("priorityFix", ""),
        },
        "firstImpression": {k: fi_scores.get(src) for k, src in _FI_MAP.items()},
        "accessibility": {
            "score": acc.get("overall_score"),
            "findings": [f.get("detail", "") for f in (acc.get("findings") or [])],
        },
        "viewport": {"findings": _viewport_rows(viewport)},
        "heuristics": [
            {
                "severity": _SEV_EN.get(h.get("severity"), "minor"),
                "id": h.get("heuristic_id"),
                "evidence": h.get("evidence", ""),
                "recommendation": h.get("recommendation", ""),
            }
            for h in heuristics
        ],
        "survey": _survey_rows(survey, q_prompts),
    }


def _pagespeed_perf(run_dir: Path) -> int | None:
    ps = _read_json(run_dir / "pagespeed.json", {}) or {}
    for key in ("desktop", "mobile"):
        score = (ps.get(key) or {}).get("performance_score")
        if score is not None:
            return score
    return None


def build_run(run_id: str, runs_root: Path = RUNS_ROOT) -> dict | None:
    run_dir = runs_root / run_id
    manifest = read_manifest(run_dir)
    if manifest is None:
        return None

    questionnaire = manifest.get("questionnaire") or {}
    q_prompts = {
        q.get("id"): q.get("prompt", "")
        for q in (questionnaire.get("questions") or [])
        if q.get("id")
    }

    personas = [build_persona(d, q_prompts) for d in _persona_dirs(run_dir)]

    meta = {
        "site": manifest.get("site", ""),
        "startUrl": manifest.get("start_url", ""),
        "intent": manifest.get("intent", ""),
        "date": _fmt_date(manifest.get("started_at")),
        "duration": _fmt_duration(manifest.get("duration_seconds")),
        "personaCount": len(personas),
        "maxSteps": manifest.get("max_steps"),
        "status": manifest.get("status", "done"),
        "pagespeed": {
            "performance": _pagespeed_perf(run_dir),
            "accessibility": None,
            "bestPractices": None,
            "seo": None,
        },
    }

    # Full aggregated narrative report (narrative + statistics + diagnostics), if present.
    report = _read_json(run_dir / "aggregated_report.json", None)
    return {"meta": meta, "personas": personas, "report": report}


def _parse_action(entry: Any) -> dict:
    """action_trace entries may be dicts or JSON strings — normalize to a dict."""
    if isinstance(entry, str):
        try:
            return json.loads(entry)
        except Exception:
            return {"description": entry}
    return entry if isinstance(entry, dict) else {}


def _latest_observation_text(trace_dir: Path) -> str:
    obs_dir = trace_dir / "observation_trace"
    if not obs_dir.is_dir():
        return ""
    files = list(obs_dir.glob("observation_trace_*.txt"))
    if not files:
        return ""
    latest = max(files, key=lambda p: int(p.stem.rsplit("_", 1)[-1]) if p.stem.rsplit("_", 1)[-1].isdigit() else -1)
    try:
        return latest.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


# Memory kinds that represent the agent "thinking" (vs raw observation dumps).
_REASONING_KINDS = ("plan", "thought", "reflection", "action")


def _live_persona(trace_dir: Path) -> dict:
    store = TraceStore(trace_dir)
    card = store.read_persona_card() or {}
    basic = store.read_basic_info()
    actions = store.read_action_trace() or []
    observations = store.read_observations()
    memories = store.read_memory_trace() or []

    last_action = _parse_action(actions[-1]) if actions else {}

    url, title = "", ""
    for obs in reversed(observations):
        for tab in obs.get("tabs") or []:
            if tab.get("is_active") or tab.get("url"):
                url = tab.get("url", "")
                title = tab.get("title", "")
                break
        if url:
            break

    # most-recent reasoning pieces, newest first
    stream = [
        {"kind": m.get("kind"), "content": str(m.get("content", ""))[:1500]}
        for m in memories
        if m.get("kind") in _REASONING_KINDS and str(m.get("content", "")).strip()
    ][-12:]
    stream.reverse()

    name = card.get("name") or "Persona"
    return {
        "id": trace_dir.name,
        "name": name,
        "step": len(actions),
        "url": url,
        "title": title,
        "action": last_action.get("description", ""),
        "observation": _latest_observation_text(trace_dir),
        "stream": stream,
        "has_screenshot": bool(list((trace_dir / "screenshot").glob("screenshot_*.png"))),
    }


def live_run(run_id: str, runs_root: Path = RUNS_ROOT) -> dict | None:
    """Per-persona live state for an in-progress run (reads trace files incrementally)."""
    run_dir = runs_root / run_id
    if not run_dir.is_dir():
        return None
    return {"personas": [_live_persona(d) for d in _persona_dirs(run_dir)]}


def latest_screenshot(run_id: str, persona_id: str, runs_root: Path = RUNS_ROOT) -> Path | None:
    shot_dir = runs_root / run_id / persona_id / "screenshot"
    if not shot_dir.is_dir():
        return None
    # prefer the non-full-page viewport shots; fall back to any
    shots = [p for p in shot_dir.glob("screenshot_*.png") if "full_page" not in p.name]
    if not shots:
        shots = list(shot_dir.glob("screenshot_*.png"))
    if not shots:
        return None
    return max(shots, key=lambda p: p.stat().st_mtime)


def _run_overall_score(run_dir: Path) -> float:
    agg = _read_json(run_dir / "aggregated_report.json", {}) or {}
    narrative = agg.get("narrative") or {}
    score = narrative.get("overall_ux_score")
    try:
        return round(float(score), 1)
    except (TypeError, ValueError):
        return 0.0


def list_runs(runs_root: Path = RUNS_ROOT) -> list[dict]:
    """Return run-history rows, newest first."""
    if not runs_root.is_dir():
        return []

    rows: list[dict] = []
    for run_dir in runs_root.iterdir():
        manifest = read_manifest(run_dir)
        if manifest is None:
            continue

        personas = [TraceStore(d) for d in _persona_dirs(run_dir)]
        outcomes = {"success": 0, "partial": 0, "failed": 0}
        critical = 0
        for store in personas:
            refl = store.read_objective_reflection() or {}
            oc = refl.get("outcome", "partial")
            if oc in outcomes:
                outcomes[oc] += 1
            for h in store.read_heuristic_findings() or []:
                if h.get("severity") == "krytyczny":
                    critical += 1

        status = manifest.get("status", "done")
        complete = status == "done"
        rows.append({
            "id": manifest.get("run_id", run_dir.name),
            "site": manifest.get("site", ""),
            "date": _fmt_date(manifest.get("started_at")),
            "started_at": manifest.get("started_at", ""),
            "personas": len(personas),
            "status": "complete" if complete else status,
            "success": outcomes["success"],
            "partial": outcomes["partial"],
            "failed": outcomes["failed"],
            "critical": critical,
            "score": _run_overall_score(run_dir) if complete else 0.0,
        })

    rows.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    if rows:
        rows[0]["current"] = True
    return rows
