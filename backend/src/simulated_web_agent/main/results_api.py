"""
results_api — pure transforms from on-disk run artifacts to dashboard payloads.

The Flask layer (app.py) stays thin: it calls these functions and JSON-encodes the
result. Everything here reads the filesystem via TraceStore + run_manifest.json and
maps it to the shapes the frontend consumes (see frontend/src/types.ts).

Layout produced by run.py:
    runs/<run_id>/run_manifest.json
    runs/<run_id>/aggregated_report.{json,md,html}
    runs/<run_id>/pagespeed.json
    runs/<run_id>/<persona_trace>/...   (one folder per persona)
"""
from __future__ import annotations

import copy
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from . import run_storage
from .iso_metrics import _numeric_survey_avg, current_effectiveness, effective_outcome
from .persona_matrix import build_persona_task_matrix
# The report's own definitions. The dashboard used to average survey answers
# its own way, so a placeholder "3" from a persona who never reached payment
# ("nie mogę ocenić") sat in the dashboard's averages after the report had
# stopped counting it. Importing the predicate and the aggregation, rather than
# copying them, is what keeps the two from drifting apart again.
from .reporter import _not_assessable, aggregate_statistics
from .run import read_manifest
from .severity import SEVERITY_EN, excluded_from_report, severity_score
from .trace_store import TraceStore, parse_action as _parse_action

log = logging.getLogger(__name__)

RUNS_ROOT = Path("runs")

# Polish severity -> dashboard severity

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


from .redaction import clean as _redact


def _answer_not_assessable(a: dict) -> bool:
    """reporter._not_assessable on one survey answer.

    The fields aggregate_statistics reads (value, then reason or reasoning), so
    a persona's row and the run's averages agree on each answer. The legacy
    `answer` field stands in for a missing `value`, as it does for the row's
    displayed answer; every archived answer has `value`.
    """
    return _not_assessable(a.get("value", a.get("answer", "")),
                           a.get("reason") or a.get("reasoning"))


def _survey_rows(survey: dict, q_prompts: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    for a in (survey or {}).get("answers", []) or []:
        if not isinstance(a, dict):
            continue
        qid = a.get("question_id", "")
        # The dashboard is a client-facing surface too. Survey answers are free
        # text from the persona and have quoted this tool's internal field names
        # verbatim ("last_action_error"); aggregate_statistics redacts its own
        # copy, and this is the other route to a reader.
        row = _redact({
            "q": q_prompts.get(qid, a.get("prompt") or qid),
            "answer": str(a.get("value", a.get("answer", ""))),
            "reasoning": a.get("reason", a.get("reasoning", "")),
        })
        # A placeholder, not a rating: the persona said it could not answer.
        # Flagged per row so the persona card can say so and no client-side
        # average can pick the placeholder up.
        row["notAssessable"] = _answer_not_assessable(a)
        rows.append(row)
    return rows


def _dashboard_iso(iso: Any, survey: dict) -> Any:
    """iso_metrics with not-assessable survey answers taken out of satisfaction.

    iso_metrics averages every 1-5 answer into synthetic_score_1_5 and copies
    the SEQ / confidence answers verbatim, placeholders included — the same "3"
    the report no longer counts. Recomputed here with iso_metrics' own average
    over the answers the report would keep; the stored file is not rewritten.
    """
    if not isinstance(iso, dict) or not isinstance(iso.get("satisfaction"), dict):
        return iso
    answers = [a for a in _as_list(_as_dict(survey).get("answers")) if isinstance(a, dict)]
    placeholders = [a for a in answers if _answer_not_assessable(a)]
    if not placeholders:
        return iso
    kept = {**_as_dict(survey), "answers": [a for a in answers if not _answer_not_assessable(a)]}
    sat = dict(iso["satisfaction"])
    sat["synthetic_score_1_5"] = _numeric_survey_avg(kept)
    na_ids = {a.get("question_id") for a in placeholders}
    if "synthetic_seq" in na_ids:
        sat["synthetic_seq_1_7"] = None
    if "synthetic_confidence" in na_ids:
        sat["synthetic_confidence_1_7"] = None
    sat["not_assessable"] = len(placeholders)
    return {**iso, "satisfaction": sat}


def _as_dict(v) -> dict:
    """The LLM occasionally returns the wrong JSON shape (observed: a bare list
    of weakness strings written to first_impression.json). One malformed file
    must degrade that section, not 500 the whole report."""
    return v if isinstance(v, dict) else {}


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def _effective_outcome(store) -> str:
    """The per-persona outcome the UI/history should count: the OBSERVED,
    evidence-grounded verdict (§4.2, iso_metrics effectiveness) where the target
    was decidable, else the LLM's declared self-rating. One definition for the
    persona payload, the history counts and the run comparison."""
    # The report's own definition (iso_metrics.effective_outcome). An
    # abandoned session used to show the model's "partial" here; it did not
    # complete, so the dashboard shows it as not completed.
    outcome, _source = effective_outcome(store)
    return "failed" if outcome == "persona_abandoned" else outcome


def build_persona(trace_dir: Path, q_prompts: dict[str, str]) -> dict:
    store = TraceStore(trace_dir)
    card = _as_dict(store.read_persona_card())
    basic = _as_dict(store.read_basic_info())
    reflection = _as_dict(store.read_objective_reflection())
    fi = _as_dict(store.read_first_impression())
    acc = _as_dict(store.read_accessibility_findings())
    heuristics = [h for h in _as_list(store.read_heuristic_findings()) if isinstance(h, dict)]
    survey = _as_dict(store.read_survey_result())
    steps = len(_as_list(store.read_action_trace()))

    demographics = _as_dict(card.get("demographics"))
    fi_scores = _as_dict(fi.get("scores"))

    # Task outcome: prefer the OBSERVED, evidence-grounded verdict (§4.2,
    # iso_metrics effectiveness) over the LLM's declared self-rating; fall back
    # to declared where the target was undecidable. Surface both + the source so
    # the UI can label "zmierzone" vs "deklarowane" and flag disagreement.
    #
    # `outcome` MUST come from _effective_outcome — the single definition shared
    # with list_runs() and _build_comparison(). A previous inline copy here
    # dropped its empty-observed_outcome fallback, so a persona's detail card
    # could disagree with its own history row.
    # Effectiveness as the current code computes it, as effective_outcome does,
    # so the source and rungs on the card match the outcome shown with them;
    # satisfaction and efficiency pass through as stored.
    iso = _as_dict(store.read_iso_metrics())
    eff = _as_dict(current_effectiveness(store))
    if iso:
        iso = {**iso, "effectiveness": eff}
    declared_outcome = reflection.get("outcome") or "unknown"
    outcome = _effective_outcome(store)

    return {
        "id": trace_dir.name,
        "name": card.get("name", "Unknown"),
        "objective": card.get("objective") or basic.get("intent", ""),
        "homeLocation": card.get("home_location"),
        "demographics": demographics,
        "metaParts": [str(v) for v in demographics.values()],
        "outcome": outcome,
        "outcomeSource": effective_outcome(store)[1],
        "declaredOutcome": declared_outcome,
        "targetRung": eff.get("target_rung"),
        "reachedRung": eff.get("reached_rung"),
        "outcomeDisagreement": bool(eff.get("disagreement")),
        "steps": steps,
        "duration": _persona_duration(trace_dir),
        "reflection": _redact({
            "summary": reflection.get("summary", ""),
            "blockers": reflection.get("blockers", []),
            "wins": reflection.get("wins", []),
            "priorityFix": reflection.get("priorityFix", ""),
        }),
        "firstImpression": {k: fi_scores.get(src) for k, src in _FI_MAP.items()},
        # Why each score, through this persona's eyes. Older runs lack both.
        "firstImpressionRationale": {
            k: _as_dict(fi.get("score_rationale")).get(src)
            for k, src in _FI_MAP.items()
        },
        "firstImpressionLens": fi.get("persona_lens"),
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
        "heuristics": [
            {
                "severity": SEVERITY_EN.get(h.get("severity"), "minor"),
                "id": h.get("heuristic_id"),
                "evidence": h.get("evidence", ""),
                "recommendation": h.get("recommendation", ""),
                "step": h.get("step"),
                "url": h.get("url", ""),
                "hasScreenshot": bool(h.get("screenshot")),
                "observationQuote": h.get("observation_quote", ""),
                "verified": bool(h.get("verified")),
                "harnessSuspect": bool(h.get("harness_suspect")),
                # Held for repeated clicks that did change the page — not a
                # failure of the tool (attribution.progress_run_steps).
                "repeatedWithProgress": h.get("repeated_with_progress") or None,
                "requiresHumanCheck": bool(h.get("requires_human_check")),
                "confidence": h.get("confidence", "medium"),
                # Set when evidence capped an over-claimed confidence, so the
                # chip can say why instead of just showing a lower level.
                "confidenceCappedBy": h.get("confidence_capped_by"),
                # The truth gate: did the claim survive re-checking against the
                # cited step's full observation? (supported/partial/unsupported)
                "claimVerdict": h.get("claim_verdict"),
                "claimVerdictReason": h.get("claim_verdict_reason"),
                # The cited page was outside the site under test.
                "offSite": bool(h.get("off_site")),
                "requiresRealUserValidation": bool(h.get("requires_real_user_validation", True)),
                "severityScore": severity_score(h),
                "severityHumanConfirmed": h.get("severity_human_confirmed"),
            }
            for h in heuristics
        ],
        "survey": _survey_rows(survey, q_prompts),
        "iso": _dashboard_iso(iso, survey),
        "biasAudit": store.read_bias_audit(),
        "cognitiveWalkthrough": store.read_cognitive_walkthrough(),
    }


def _pagespeed_perf(run_dir: Path) -> tuple[int | None, str | None]:
    """The PageSpeed score the tile shows, and for which device.

    Mobile first, the one the score counts (scoring._performance_line). It used
    to prefer desktop, so the tile showed 38/100 for a run whose score had set
    PageSpeed aside because the mobile measurement failed.
    """
    ps = _read_json(run_dir / "pagespeed.json", {}) or {}
    for key in ("mobile", "desktop"):
        score = (ps.get(key) or {}).get("performance_score")
        if score is not None:
            return score, key
    return None, None


def _first_persona_site_context(run_dir: Path) -> str:
    """site_context as seen by the judgement passes, recovered from the first
    persona's basic_info. Used for runs that pre-date recording it in the
    manifest — it always lived here, only the manifest was missing it."""
    for d in _persona_dirs(run_dir):
        ctx = _as_dict(_read_json(d / "basic_info.json", {})).get("site_context")
        if isinstance(ctx, str) and ctx.strip():
            return ctx.strip()
    return ""


def _questionnaire_questions(manifest: dict) -> list[dict]:
    """The manifest's questionnaire questions. Read the way the reporter reads
    them, which also accepts a questionnaire stored as a JSON string."""
    qn = manifest.get("questionnaire")
    if isinstance(qn, str):
        try:
            qn = json.loads(qn)
        except ValueError:
            qn = None
    return [q for q in _as_list(_as_dict(qn).get("questions")) if isinstance(q, dict)]


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _score_breakdown(report: dict | None) -> dict | None:
    """statistics.score_breakdown (scoring.compute_ux_score), or None.

    None for runs made before scoring.py, and for a breakdown missing the
    numbers the "Jak obliczono ocenę" table prints — the dashboard then shows
    no breakdown rather than one with invented lines.
    """
    stats = _as_dict(_as_dict(report).get("statistics"))
    b = _as_dict(stats.get("score_breakdown"))
    if not all(_is_number(b.get(k)) for k in ("score", "start", "total_deduction", "raw")):
        return None
    return b


def _score_value(v) -> float | None:
    """A score fit to print, rounded as the report prints it, or None."""
    if not _is_number(v) and not isinstance(v, str):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # A NaN would also make the payload invalid JSON for the browser; zero or
    # less is no score on a 1-5 scale (scoring.FLOOR is 1.0).
    return round(f, 1) if math.isfinite(f) and f > 0 else None


def _report_score(report: dict | None) -> float | None:
    """The overall UX score exactly as the report shows it.

    reporter.resolve_narrative's rule: statistics.score_breakdown.score whenever
    the run has one — narrative.overall_ux_score is forced to it only when the
    aggregate LLM call succeeded, so the breakdown is the one place it is always
    right — else the score the run was delivered with. None when there is
    neither, never 0.0: a zero read as "the worst possible site".

    One deliberate difference: for a pre-scoring run whose aggregate LLM call
    failed, the report's stats-only fallback narrative prints the
    first-impression average as "Ocena UX". That is not a UX score, and the
    dashboard does not render that fallback narrative either, so it shows "—".
    (No archived run is in that state.)
    """
    stats = _as_dict(_as_dict(report).get("statistics"))
    computed = _as_dict(stats.get("score_breakdown")).get("score")
    if computed is not None:
        return _score_value(computed)
    narrative = _as_dict(_as_dict(report).get("narrative"))
    if "error" in narrative:
        return None
    return _score_value(narrative.get("overall_ux_score"))


def _recompute_survey(run_dir: Path, q_meta: list[dict]) -> dict | None:
    """statistics.survey as the current reporter would build it for this run.

    The same inputs (each persona's ux_report.json, the manifest's questions)
    through the same function, aggregate_statistics — so the not-assessable
    rule, the short-session exclusion and the question order are the report's
    own, not a copy. It is pure and reads only the run's files (~50 ms on the
    largest archived run). None when there is nothing to aggregate or the
    aggregation fails; the survey tab then falls back to the per-persona rows,
    which carry the same not-assessable flag.
    """
    stores = [TraceStore(d) for d in _persona_dirs(run_dir)]
    reports = [r for r in (s.read_ux_report() for s in stores) if isinstance(r, dict) and r]
    if not reports:
        return None
    try:
        stats = aggregate_statistics(reports, q_meta=q_meta or None, stores=stores)
    except Exception:
        log.exception("[results_api] survey recompute failed for %s", run_dir.name)
        return None
    survey = _as_dict(_as_dict(stats).get("survey"))
    return survey or None


def _run_survey(run_dir: Path, report: dict | None, q_meta: list[dict]) -> dict | None:
    """Run-level survey figures for the dashboard, not-assessable answers apart.

    A report built since the not-assessable fix stores `survey.not_assessable`,
    and its figures are used as they are — the dashboard then shows the same
    numbers as that report. Every archived report predates the fix and averages
    the placeholders in, so for those the figures are recomputed from the
    session files by the report's own aggregation.
    """
    stored = _as_dict(_as_dict(_as_dict(report).get("statistics")).get("survey"))
    if "not_assessable" in stored and isinstance(stored.get("questions"), list):
        source, survey = "report", stored
    else:
        source, survey = "traces", _recompute_survey(run_dir, q_meta)
    if survey is None:
        return None
    na = {str(k): int(v) for k, v in _as_dict(survey.get("not_assessable")).items()
          if _is_number(v) and v > 0}
    return {
        "source": source,
        "questions": [q for q in _as_list(survey.get("questions")) if isinstance(q, dict)],
        "notAssessable": na,
        "notAssessableTotal": sum(na.values()),
        "surveysIncluded": survey.get("surveys_included"),
        "excludedSurveys": [e for e in _as_list(survey.get("excluded_surveys"))
                            if isinstance(e, dict)],
    }


def _report_issue_counts(report: dict | None) -> dict | None:
    """The issue counts the report itself prints, and how many it holds back.

    The dashboard counted every per-persona finding, including those the gates
    held back, so its overview said "Analiza wykazała 0/4/4" above a report
    listing 0/0/2 (2026-09-24 hyundai; 8 of 9 archived runs disagreed). These
    are the report's own numbers: its narrative resolved exactly as the HTML
    and Word renderers resolve it — WCAG demotions included — then counted as
    the header counts it. None when the run has no report.
    """
    if not isinstance(report, dict) or not (report.get("narrative") or report.get("statistics")):
        return None
    from .reporter import resolve_narrative
    from .reporter_html import _issue_counts

    # resolve_narrative writes into the narrative; the payload keeps the stored one.
    counts = _issue_counts(resolve_narrative(copy.deepcopy(report)))
    sc = _as_dict(_as_dict(_as_dict(report.get("statistics")).get("heuristic")).get("severity_counts"))
    held = sc.get("needs_verification")
    counts["needs_verification"] = int(held) if _is_number(held) else 0
    return counts


def build_run(run_id: str, runs_root: Path = RUNS_ROOT) -> dict | None:
    # Opening a run this instance did not execute (or executed before a
    # restart) fetches it from the bucket first; a no-op when local or when no
    # bucket is configured.
    run_storage.ensure_local(run_id, runs_root)
    run_dir = runs_root / run_id
    manifest = read_manifest(run_dir)
    if manifest is None:
        return None

    q_meta = _questionnaire_questions(manifest)
    q_prompts = {q.get("id"): q.get("prompt", "") for q in q_meta if q.get("id")}

    personas = [build_persona(d, q_prompts) for d in _persona_dirs(run_dir)]

    ps_score, ps_device = _pagespeed_perf(run_dir)
    meta = {
        "site": manifest.get("site", ""),
        "startUrl": manifest.get("start_url", ""),
        "intent": manifest.get("intent", ""),
        "date": _fmt_date(manifest.get("started_at")),
        "duration": _fmt_duration(manifest.get("duration_seconds")),
        "personaCount": len(personas),
        "maxSteps": manifest.get("max_steps"),
        "status": manifest.get("status", "done"),
        # §6.1 Scope: which provider/models/prompts produced these findings.
        # None for runs made before scope was recorded — a report that guessed
        # would be worse than one that says it does not know.
        "scope": _as_dict(manifest.get("scope")) or None,
        # The declared site purpose the judgements were calibrated against.
        # Older runs never stored it (it lived only in each persona's basic_info),
        # so fall back to reading it off the first persona when absent.
        "siteContext": manifest.get("site_context") or _first_persona_site_context(run_dir),
        "pagespeed": {
            "performance": ps_score,
            "device": ps_device,
            "accessibility": None,
            "bestPractices": None,
            "seo": None,
        },
    }

    # Full aggregated narrative report (narrative + statistics + diagnostics), if present.
    report = _read_json(run_dir / "aggregated_report.json", None)
    comparison = _build_comparison(run_id, manifest, report, personas, runs_root)
    return {
        "meta": meta,
        "personas": personas,
        "report": report,
        "comparison": comparison,
        # §6.1 persona x task section. Derived from `personas` rather than
        # stored, so it is correct for old runs too.
        "personaTasks": build_persona_task_matrix(personas),
        # The headline score and the arithmetic behind it, from the one place
        # the report takes them (see _report_score). Both None for runs that
        # predate what they need; the screens then print "—" and no breakdown.
        "score": _report_score(report),
        # The report's own issue counts, for the overview (see _report_issue_counts).
        "issueCounts": _report_issue_counts(report),
        "scoreBreakdown": _score_breakdown(report),
        # Survey averages and distributions without the not-assessable answers,
        # which are counted separately (see _run_survey).
        "survey": _run_survey(run_dir, report, q_meta),
    }


def _issue_key(issue: dict) -> str | None:
    """Stable identity for a narrative issue across runs.

    root_cause_id when the clustering pass assigned one, else the Nielsen
    number. Keying on heuristic_id alone gave only ten possible buckets, so
    every distinct issue filed under H4 collapsed into one and "resolved" meant
    "nothing was filed under that Nielsen number this time" rather than "a
    defect was fixed" — the exact conflation root_cause.py exists to prevent.
    """
    rc = issue.get("root_cause_id")
    if rc:
        return f"rc:{rc}"
    h_id = issue.get("heuristic_id")
    return f"h:{h_id}" if h_id is not None else None


def _comparable_issues(report: dict | None) -> list[dict]:
    """The critical and serious issues the report shows, with a kind and content.

    Read through resolve_narrative, as the report is. The kind is the
    root-cause id, the Nielsen number, or for an accessibility issue its topic
    — WCAG issues carry no Nielsen number and used to be skipped, so the one
    critical issue of the 2026-09-26 run was listed as "resolved" against the
    run before, where the same barrier had the id "A11Y-1".
    """
    if not isinstance(report, dict) or not report.get("narrative"):
        return []
    from .a11y_checks import a11y_topic
    from .attribution import _content_stems
    from .reporter import _is_wcag_issue, resolve_narrative

    narrative = resolve_narrative(copy.deepcopy(report))
    out = []
    for key in ("critical_issues", "serious_issues"):
        for issue in narrative.get(key) or []:
            if not isinstance(issue, dict):
                continue
            body = " ".join(str(issue.get(k) or "") for k in ("impact", "recommendation"))
            kind = (f"wcag:{a11y_topic(body) or 'inne'}" if _is_wcag_issue(issue)
                    else _issue_key(issue))
            if kind is None:
                continue
            out.append({"kind": kind, "stems": _content_stems(body),
                        "text": (issue.get("impact") or issue.get("heuristic_name") or "")[:160]})
    return out


# Shared distinctive stems for two issues of one kind to count as the same
# issue. Measured: the same test-drive combobox in two runs shares 21; two
# unrelated H2 issues (Spanish text; a colour's price) share 6, half of them
# boilerplate. Same Nielsen number alone is not the same defect.
_SAME_ISSUE_STEMS = 8


def _match_issues(now: list[dict], prev: list[dict]) -> tuple[list, list, list]:
    """(fresh, persisting, not reported again) by kind and content."""
    matched_prev: set[int] = set()
    fresh, persisting = [], []
    for n in now:
        hit = next((j for j, p in enumerate(prev) if j not in matched_prev
                    and p["kind"] == n["kind"]
                    and len(p["stems"] & n["stems"]) >= _SAME_ISSUE_STEMS), None)
        if hit is None:
            fresh.append(n["text"])
        else:
            matched_prev.add(hit)
            persisting.append(n["text"])
    gone = [p["text"] for j, p in enumerate(prev) if j not in matched_prev]
    return fresh, persisting, gone


def _trend(now, prev, higher_is_better=True) -> str:
    """One vocabulary: "better" | "worse" | "flat".

    It used to return up/down for higher-is-better metrics and better/worse for
    the rest, so a single field carried five values from two dialects and every
    consumer had to handle both. The direction is already encoded by
    higher_is_better — the caller wants to know whether the number moved the
    right way, not which way it moved.

    Module level rather than nested inside _build_comparison so it can be
    tested directly; it is a pure function of two numbers.
    """
    if now is None or prev is None or now == prev:
        return "flat"
    improved = (now > prev) if higher_is_better else (now < prev)
    return "better" if improved else "worse"


def _rate(ok, total):
    """Success rate, or None when there is nothing to divide by."""
    return (ok / total) if total else None


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

    # The same score the history row and the report header show, so a trend
    # never compares a number that neither run's report printed.
    _score = _report_score

    def _sev_counts(rep):
        stats = (rep or {}).get("statistics") or {}
        return (stats.get("heuristic") or {}).get("severity_counts") or {}

    def _successes(run_dir_):
        n_ok, n_all = 0, 0
        for pd in _persona_dirs(run_dir_):
            n_all += 1
            if _effective_outcome(TraceStore(pd)) == "success":
                n_ok += 1
        return n_ok, n_all

    now_score, prev_score = _score(report), _score(prev_report)
    now_sc, prev_sc = _sev_counts(report), _sev_counts(prev_report)
    now_ok, now_all = sum(1 for p in personas if p.get("outcome") == "success"), len(personas)
    prev_ok, prev_all = _successes(prev_dir)

    # What each report printed. severity_counts also holds needs_verification,
    # so summing it made 2 confirmed issues against 2 read "8 vs 7 → worse".
    def _crit_total(rep, sc):
        c = _report_issue_counts(rep)
        if c is not None:
            return c["critical"], c["total"]
        return sc.get("krytyczny", 0), sum(sc.get(k, 0) for k in ("krytyczny", "poważny", "drobny"))

    now_crit, now_total = _crit_total(report, now_sc)
    prev_crit, prev_total = _crit_total(prev_report, prev_sc)

    metrics = [
        {"label": "Overall score", "now": now_score if now_score is not None else "—",
         "prev": prev_score if prev_score is not None else "—", "trend": _trend(now_score, prev_score)},
        # Compared as a rate, because it is displayed as one: trending on the
        # raw numerator labelled 2/2 after 3/8 a regression.
        {"label": "Task success", "now": f"{now_ok}/{now_all}", "prev": f"{prev_ok}/{prev_all}",
         "trend": _trend(_rate(now_ok, now_all), _rate(prev_ok, prev_all))},
        {"label": "Critical findings", "now": now_crit, "prev": prev_crit,
         "trend": _trend(now_crit, prev_crit, higher_is_better=False)},
        {"label": "Total findings", "now": now_total, "prev": prev_total,
         "trend": _trend(now_total, prev_total, higher_is_better=False)},
    ]

    fresh, persisting, resolved = _match_issues(_comparable_issues(report),
                                                _comparable_issues(prev_report))

    return {
        "vsDate": _fmt_date(prev_manifest.get("started_at")),
        "vsRunId": prev_dir.name,
        "metrics": metrics,
        "fresh": fresh[:4],
        "persisting": persisting[:4],
        "resolved": resolved[:4],
    }




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


def _run_overall_score(run_dir: Path) -> float | None:
    """The history row's score: the report's own (see _report_score).

    It used to read narrative.overall_ux_score alone and print 0.0 when that
    was missing, so a run whose aggregate LLM call failed showed a red 0.0 in
    the history beside a report headed by its computed score.
    """
    return _report_score(_read_json(run_dir / "aggregated_report.json", None))


def build_history_row(run_dir: Path) -> dict | None:
    """One history row for a run directory, or None if it isn't a run.

    Extracted from list_runs so the same row can be computed once at upload
    time and stored in the bucket's shared index — the history screen then
    costs one small read instead of hydrating every run from storage.
    """
    manifest = read_manifest(run_dir)
    if manifest is None:
        return None

    personas = [TraceStore(d) for d in _persona_dirs(run_dir)]
    outcomes = {"success": 0, "partial": 0, "failed": 0}
    critical = 0
    for store in personas:
        oc = _effective_outcome(store)
        if oc in outcomes:
            outcomes[oc] += 1
        for h in store.read_heuristic_findings() or []:
            # Gated, like every other headline count. Counting raw findings
            # here meant the history list painted a red "4 critical" for a
            # run whose report correctly reported 0 — the four had been
            # rejected as harness-suspect, off-site or claim-unsupported.
            # severity.excluded_from_report is the shared predicate exactly
            # so these two views cannot drift apart.
            if h.get("severity") == "krytyczny" and not excluded_from_report(h):
                critical += 1

    # The report's own critical count where there is a report: counting
    # confirmed per-persona findings still counted one defect once per persona
    # who filed it, against a report that lists it once.
    report_counts = _report_issue_counts(_read_json(run_dir / "aggregated_report.json", None))
    if report_counts is not None:
        critical = report_counts["critical"]

    status = manifest.get("status", "done")
    complete = status == "done"
    return {
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
        "score": _run_overall_score(run_dir) if complete else None,
        "failure": manifest.get("failure"),
    }


def list_runs(runs_root: Path = RUNS_ROOT) -> list[dict]:
    """Run-history rows, newest first — this instance's disk plus the bucket.

    On Cloud Run the local disk holds only the runs this container happened to
    execute; everything older lives in the bucket. Merging both is what makes
    history survive a restart. A locally present run wins, since it may still
    be in progress and therefore newer than its uploaded row.
    """
    rows: list[dict] = []
    seen: set[str] = set()

    if runs_root.is_dir():
        for run_dir in runs_root.iterdir():
            row = build_history_row(run_dir)
            if row:
                rows.append(row)
                seen.add(row["id"])

    for row in run_storage.read_index():
        if row.get("id") and row["id"] not in seen:
            rows.append(row)
            seen.add(row["id"])

    rows.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    if rows:
        rows[0]["current"] = True
    return rows
