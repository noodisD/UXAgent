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
from .severity import severity_score
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


def _as_dict(v) -> dict:
    """The LLM occasionally returns the wrong JSON shape (observed: a bare list
    of weakness strings written to first_impression.json). One malformed file
    must degrade that section, not 500 the whole report."""
    return v if isinstance(v, dict) else {}


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def build_persona(trace_dir: Path, q_prompts: dict[str, str]) -> dict:
    store = TraceStore(trace_dir)
    card = _as_dict(store.read_persona_card())
    basic = _as_dict(store.read_basic_info())
    reflection = _as_dict(store.read_objective_reflection())
    fi = _as_dict(store.read_first_impression())
    acc = _as_dict(store.read_accessibility_findings())
    viewport = _as_list(store.read_viewport_findings())
    heuristics = [h for h in _as_list(store.read_heuristic_findings()) if isinstance(h, dict)]
    survey = _as_dict(store.read_survey_result())
    steps = len(_as_list(store.read_action_trace()))

    demographics = _as_dict(card.get("demographics"))
    fi_scores = _as_dict(fi.get("scores"))

    return {
        "id": trace_dir.name,
        "name": card.get("name", "Unknown"),
        "objective": card.get("objective") or basic.get("intent", ""),
        "homeLocation": card.get("home_location"),
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
            "findings": [
                {
                    "detail": f.get("detail", ""),
                    "step": f.get("step"),
                    "url": f.get("url", ""),
                    "hasScreenshot": bool(f.get("screenshot")),
                    "observationQuote": f.get("observation_quote", ""),
                    "verified": bool(f.get("verified")),
                }
                for f in (acc.get("findings") or [])
                if isinstance(f, dict)
            ],
        },
        "viewport": {"findings": _viewport_rows(viewport)},
        "heuristics": [
            {
                "severity": _SEV_EN.get(h.get("severity"), "minor"),
                "id": h.get("heuristic_id"),
                "evidence": h.get("evidence", ""),
                "recommendation": h.get("recommendation", ""),
                "step": h.get("step"),
                "url": h.get("url", ""),
                "hasScreenshot": bool(h.get("screenshot")),
                "observationQuote": h.get("observation_quote", ""),
                "verified": bool(h.get("verified")),
                "harnessSuspect": bool(h.get("harness_suspect")),
                "requiresHumanCheck": bool(h.get("requires_human_check")),
                "confidence": h.get("confidence", "medium"),
                "requiresRealUserValidation": bool(h.get("requires_real_user_validation", True)),
                "severityScore": severity_score(h),
                "severityHumanConfirmed": h.get("severity_human_confirmed"),
            }
            for h in heuristics
        ],
        "survey": _survey_rows(survey, q_prompts),
        "iso": store.read_iso_metrics(),
        "biasAudit": store.read_bias_audit(),
        "cognitiveWalkthrough": store.read_cognitive_walkthrough(),
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
    comparison = _build_comparison(run_id, manifest, report, personas, runs_root)
    return {"meta": meta, "personas": personas, "report": report, "comparison": comparison}


def _issue_map(report: dict | None) -> dict[int, str]:
    """heuristic_id -> short impact text for all critical+serious narrative issues."""
    narrative = (report or {}).get("narrative") or {}
    out: dict[int, str] = {}
    for key in ("critical_issues", "serious_issues"):
        for issue in narrative.get(key) or []:
            if isinstance(issue, dict) and issue.get("heuristic_id") is not None:
                h_id = issue["heuristic_id"]
                text = (issue.get("impact") or issue.get("heuristic_name") or "")[:160]
                out.setdefault(h_id, text)
    return out


def _build_comparison(
    run_id: str, manifest: dict, report: dict | None, personas: list[dict], runs_root: Path
) -> dict | None:
    """Compare this run against the most recent earlier completed run of the same site."""
    site = manifest.get("site")
    started = manifest.get("started_at") or ""
    if not site or not started:
        return None

    prev_dir, prev_manifest = None, None
    for d in runs_root.iterdir() if runs_root.is_dir() else []:
        if d.name == run_id:
            continue
        m = read_manifest(d)
        if not m or m.get("site") != site or m.get("status") != "done":
            continue
        m_started = m.get("started_at") or ""
        if m_started < started and (prev_manifest is None or m_started > (prev_manifest.get("started_at") or "")):
            prev_dir, prev_manifest = d, m
    if prev_dir is None:
        return None

    prev_report = _read_json(prev_dir / "aggregated_report.json", None)

    def _score(rep):
        try:
            return float(((rep or {}).get("narrative") or {}).get("overall_ux_score"))
        except (TypeError, ValueError):
            return None

    def _sev_counts(rep):
        stats = (rep or {}).get("statistics") or {}
        return (stats.get("heuristic") or {}).get("severity_counts") or {}

    def _successes(run_dir_):
        n_ok, n_all = 0, 0
        for pd in _persona_dirs(run_dir_):
            refl = TraceStore(pd).read_objective_reflection() or {}
            n_all += 1
            if refl.get("outcome") == "success":
                n_ok += 1
        return n_ok, n_all

    now_score, prev_score = _score(report), _score(prev_report)
    now_sc, prev_sc = _sev_counts(report), _sev_counts(prev_report)
    now_ok, now_all = sum(1 for p in personas if p.get("outcome") == "success"), len(personas)
    prev_ok, prev_all = _successes(prev_dir)

    def _trend(now, prev, higher_is_better=True):
        if now is None or prev is None or now == prev:
            return "flat"
        return ("up" if now > prev else "down") if higher_is_better else ("better" if now < prev else "worse")

    now_crit = now_sc.get("krytyczny", 0)
    prev_crit = prev_sc.get("krytyczny", 0)
    now_total = sum(now_sc.values()) if now_sc else 0
    prev_total = sum(prev_sc.values()) if prev_sc else 0

    metrics = [
        {"label": "Overall score", "now": now_score if now_score is not None else "—",
         "prev": prev_score if prev_score is not None else "—", "trend": _trend(now_score, prev_score)},
        {"label": "Task success", "now": f"{now_ok}/{now_all}", "prev": f"{prev_ok}/{prev_all}",
         "trend": _trend(now_ok, prev_ok)},
        {"label": "Critical findings", "now": now_crit, "prev": prev_crit,
         "trend": _trend(now_crit, prev_crit, higher_is_better=False)},
        {"label": "Total findings", "now": now_total, "prev": prev_total,
         "trend": _trend(now_total, prev_total, higher_is_better=False)},
    ]

    now_issues, prev_issues = _issue_map(report), _issue_map(prev_report)
    fresh = [txt for h, txt in now_issues.items() if h not in prev_issues]
    persisting = [txt for h, txt in now_issues.items() if h in prev_issues]
    resolved = [txt for h, txt in prev_issues.items() if h not in now_issues]

    return {
        "vsDate": _fmt_date(prev_manifest.get("started_at")),
        "vsRunId": prev_dir.name,
        "metrics": metrics,
        "fresh": fresh[:4],
        "persisting": persisting[:4],
        "resolved": resolved[:4],
    }


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

    # Session end detection: the agent terminated itself, the session crashed,
    # or post-session analysis artifacts already exist. Without this the live
    # view keeps showing the last step as in-progress with a ticking timer.
    if (trace_dir / "error.txt").exists():
        status = "error"
    elif last_action.get("action") == "terminate" or any(
        (trace_dir / f).exists() for f in ("ux_report.json", "survey_result.json")
    ):
        status = "done"
    else:
        status = "running"
    finished = status != "running"

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
    objective = card.get("objective") or (basic or {}).get("intent", "")

    # Per-step timing from screenshot mtimes (one viewport shot per step, taken
    # right before the action executes) — powers the live step history + timers.
    import time as _time
    now = _time.time()
    shot_times: dict[int, float] = {}
    for p in (trace_dir / "screenshot").glob("screenshot_*.png"):
        if "full_page" in p.name:
            continue
        suffix = p.stem.rsplit("_", 1)[-1]
        if suffix.isdigit():
            try:
                shot_times[int(suffix)] = p.stat().st_mtime
            except OSError:
                pass

    started = min(shot_times.values()) if shot_times else None
    activity_candidates = [
        p for p in (
            trace_dir / "action_trace.json",
            trace_dir / "memory_trace.json",
            trace_dir / "api_trace",  # dir mtime bumps on every LLM call
        )
        if p.exists()
    ]
    last_activity = max(
        [p.stat().st_mtime for p in activity_candidates] + list(shot_times.values()),
        default=None,
    )

    history = []
    for i, a in enumerate(actions):
        parsed = _parse_action(a)
        t0, t1 = shot_times.get(i), shot_times.get(i + 1)
        # duration of step i = time between its snapshot and the next one;
        # for the newest step, time since its snapshot (still in progress) —
        # unless the session already ended, then cap at the last activity.
        if t0 is not None:
            if t1 is None:
                end = (last_activity or now) if finished else now
                duration = round(max(end - t0, 0.0), 1)
            else:
                duration = round(t1 - t0, 1)
        else:
            duration = None
        history.append({
            "step": i,
            "description": parsed.get("description", ""),
            "duration_s": duration,
            "current": i == len(actions) - 1 and not finished,
        })
    history = history[-8:]

    return {
        "id": trace_dir.name,
        "name": name,
        "objective": objective,
        "step": len(actions),
        "url": url,
        "title": title,
        "action": last_action.get("description", ""),
        "observation": _latest_observation_text(trace_dir),
        "stream": stream,
        "has_screenshot": bool(shot_times),
        "elapsed_seconds": round(now - started, 1) if started else None,
        "last_activity_seconds": round(now - last_activity, 1) if last_activity else None,
        "history": history,
        "status": status,
        "finished": finished,
    }


def live_run(run_id: str, runs_root: Path = RUNS_ROOT) -> dict | None:
    """Per-persona live state for an in-progress run (reads trace files incrementally)."""
    run_dir = runs_root / run_id
    if not run_dir.is_dir():
        return None
    return {"personas": [_live_persona(d) for d in _persona_dirs(run_dir)]}


def step_screenshot(run_id: str, persona_id: str, step: int, runs_root: Path = RUNS_ROOT) -> Path | None:
    """Screenshot PNG for a specific step (viewport shot, full-page fallback)."""
    shot_dir = runs_root / run_id / persona_id / "screenshot"
    for name in (f"screenshot_{step}.png", f"screenshot_{step}_full_page.png"):
        p = shot_dir / name
        if p.exists():
            return p
    return None


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
            "failure": manifest.get("failure"),
        })

    rows.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    if rows:
        rows[0]["current"] = True
    return rows
