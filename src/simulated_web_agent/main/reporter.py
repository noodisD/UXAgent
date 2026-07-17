"""
Report generation: per-agent and aggregated.

Per-agent:
  generate_per_agent_report(store) → trace_dir/ux_report.json + .md

Aggregated (called after ALL agents finish):
  generate_aggregated_report(stores, task, url) → runs/aggregated_report.json + .md
"""

import json
import logging
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..agent.gpt import async_chat
from ..agent.prompt_registry import registry as _prompts
from .pagespeed import format_pagespeed_summary, read_pagespeed
from .trace_store import TraceStore

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"krytyczny": 0, "poważny": 1, "drobny": 2}
SEVERITY_EN = {"krytyczny": "critical", "poważny": "serious", "drobny": "minor"}
HEURISTIC_NAMES = {
    1: "Widoczność statusu systemu",
    2: "Zgodność systemu z rzeczywistością",
    3: "Kontrola użytkownika i swoboda działania",
    4: "Spójność i standardy",
    5: "Zapobieganie błędom",
    6: "Rozpoznawanie zamiast zapamiętywania",
    7: "Elastyczność i efektywność użytkowania",
    8: "Estetyczny i minimalistyczny design",
    9: "Pomoc w rozpoznawaniu i naprawianiu błędów",
    10: "Pomoc i dokumentacja",
}


def _severity_sort_key(finding: dict) -> int:
    return SEVERITY_ORDER.get(finding.get("severity", "drobny"), 99)


# ---------------------------------------------------------------------------
# Per-agent report
# ---------------------------------------------------------------------------

def _build_per_agent_json(store: TraceStore) -> dict:
    basic = store.read_basic_info()
    first_impression = store.read_first_impression() or {}
    heuristic_findings: list = store.read_heuristic_findings()
    accessibility = store.read_accessibility_findings() or {}
    viewport = store.read_viewport_findings()
    survey = store.read_survey_result() or {}

    sorted_findings = sorted(heuristic_findings, key=_severity_sort_key)

    # Headline severity counts exclude harness-suspect findings (interaction
    # failures wrongly attributed to the site) so they can't inflate the numbers.
    confirmed = [f for f in heuristic_findings if not f.get("harness_suspect")]
    severity_counts = Counter(f.get("severity") for f in confirmed)
    needs_verification = sum(1 for f in heuristic_findings if f.get("harness_suspect"))

    return {
        "trace_dir": str(store.trace_dir),
        "persona": basic.get("persona", ""),
        "intent": basic.get("intent", ""),
        "first_impression": first_impression,
        "heuristic_findings": sorted_findings,
        "severity_counts": {
            "krytyczny": severity_counts.get("krytyczny", 0),
            "poważny": severity_counts.get("poważny", 0),
            "drobny": severity_counts.get("drobny", 0),
            "needs_verification": needs_verification,
        },
        "accessibility": accessibility,
        "viewport_findings": viewport,
        "survey_result": survey,
        "session_result": store.read_session_result(),
    }


def _render_md(report: dict) -> str:
    lines: list[str] = []

    def h(level: int, text: str):
        lines.append(f"{'#' * level} {text}\n")

    def p(text: str):
        lines.append(f"{text}\n")

    def ul(items: list[str]):
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    h(1, "Raport UX – ocena agenta")
    persona_text = report.get("persona", "")
    # Show first paragraph as the header line; full persona follows
    persona_first = persona_text.split("\n\n")[0] if persona_text else ""
    p(f"**Persona:** {persona_first}")
    if persona_text and "\n\n" in persona_text:
        lines.append(f"\n<details><summary>Pełna persona</summary>\n\n{persona_text}\n\n</details>\n")
    p(f"**Zadanie:** {report.get('intent', '')}")

    # First impression
    fi = report.get("first_impression") or {}
    if not isinstance(fi, dict):
        fi = {}  # malformed LLM shape (e.g. a bare list) — skip the section
    if fi and not fi.get("error"):
        h(2, "Pierwsze wrażenie (test 5 sekund)")
        p(f"**Ocena ogólna:** {fi.get('overall_score', '—')}/5")
        p(fi.get("clarity_summary", ""))
        scores = fi.get("scores") or {}
        if scores:
            h(3, "Oceny cząstkowe")
            score_labels = {
                "purpose_clarity": "Jasność celu",
                "navigation_visibility": "Widoczność nawigacji",
                "call_to_action": "Wezwanie do działania",
                "visual_hierarchy": "Hierarchia wizualna",
                "trustworthiness": "Wiarygodność",
            }
            for key, label in score_labels.items():
                lines.append(f"| {label} | {scores.get(key, '—')}/5 |")
            lines.append("")
        strengths = fi.get("strengths") or []
        weaknesses = fi.get("weaknesses") or []
        if strengths:
            h(3, "Mocne strony")
            ul(strengths)
        if weaknesses:
            h(3, "Słabe strony")
            ul(weaknesses)

    # Heuristic findings
    findings = report.get("heuristic_findings") or []
    counts = report.get("severity_counts") or {}
    h(2, "Ocena heurystyczna (Nielsen)")
    p(
        f"Znaleziono **{len(findings)}** problemów: "
        f"{counts.get('krytyczny', 0)} krytycznych, "
        f"{counts.get('poważny', 0)} poważnych, "
        f"{counts.get('drobny', 0)} drobnych."
    )
    if findings:
        for f in findings:
            severity_badge = f.get("severity", "drobny").upper()
            h(3, f"[{severity_badge}] H{f.get('heuristic_id', '?')}: {f.get('heuristic_name', '')}")
            p(f"**URL:** {f.get('url', '—')}  |  **Krok:** {f.get('step', '—')}")
            p(f"**Dowód:** {f.get('evidence', '')}")
            p(f"**Rekomendacja:** {f.get('recommendation', '')}")

    # Accessibility
    acc = report.get("accessibility") or {}
    if acc and not acc.get("error"):
        h(2, "Dostępność")
        p(f"**Ocena:** {acc.get('overall_score', '—')}/5")
        p(acc.get("summary", ""))
        acc_findings = acc.get("findings") or []
        if acc_findings:
            h(3, "Problemy z dostępnością")
            for af in acc_findings:
                p(f"**[{af.get('severity', '').upper()}]** {af.get('category', '')} — {af.get('detail', '')}")
                p(f"*Rekomendacja:* {af.get('recommendation', '')}")
        font_issues = acc.get("font_size_issues") or []
        if font_issues:
            h(3, "Problemy z rozmiarem czcionki")
            for fi_item in font_issues:
                p(f"Krok {fi_item.get('step', '?')}: min {fi_item.get('min_size_found', '?')} — {fi_item.get('detail', '')}")

    # Viewport
    vp_findings = report.get("viewport_findings") or []
    issues = [v for v in vp_findings if v.get("horizontal_overflow") or v.get("wide_elements") or v.get("small_fonts")]
    h(2, "Responsywność")
    if issues:
        for v in issues:
            p(f"**{v.get('viewport', '')} @ {v.get('url', '')}**")
            if v.get("horizontal_overflow"):
                p("⚠ Wykryto poziomy pasek przewijania (overflow).")
            if v.get("wide_elements"):
                p(f"Elementy szersze niż viewport: {', '.join(v['wide_elements'][:5])}")
            if v.get("small_fonts"):
                for sf in v["small_fonts"]:
                    p(f"Mała czcionka: {sf.get('size')}px: \"{sf.get('text', '')}\"")
    else:
        p("Nie wykryto problemów z responsywnością.")

    # Survey
    survey = report.get("survey_result") or {}
    answers = survey.get("answers") or []
    if answers:
        h(2, "Wyniki ankiety")
        for a in answers:
            qid = a.get("question_id", "")
            val = a.get("value", "")
            reason = a.get("reason", "")
            p(f"**{qid}:** {val}")
            if reason:
                p(f"*{reason}*")

    return "\n".join(lines)


async def generate_per_agent_report(store: TraceStore) -> dict:
    """Build and save per-agent ux_report.json + ux_report.md."""
    report = _build_per_agent_json(store)
    md = _render_md(report)
    store.write_ux_report(report, md)
    log.info("[reporter] Per-agent report saved to %s", store.trace_dir)
    return report


# ---------------------------------------------------------------------------
# Objective reflection (structured, dashboard-facing)
# ---------------------------------------------------------------------------

_REFLECTION_SYSTEM = """\
Jesteś starszym badaczem UX. Otrzymasz dane z sesji symulowanego użytkownika (persony)
na stronie internetowej: jej cel, oceny heurystyczne, pierwsze wrażenie, dostępność,
responsywność oraz odpowiedzi ankietowe.

Oceń z perspektywy CELU persony, czy udało się go osiągnąć, i zwróć WYŁĄCZNIE obiekt JSON
(bez markdown, bez komentarzy) o następującej strukturze:

{
  "outcome": "success | partial | failed",
  "summary": "2-4 zdania po polsku: czy persona osiągnęła swój cel i dlaczego",
  "blockers": ["konkretna bariera związana z celem", "..."],
  "wins": ["co realnie pomogło personie", "..."],
  "priorityFix": "jedna najważniejsza rzecz do naprawienia, konkretna (UI/przepływ/treść)"
}

Zasady:
- "outcome" MUSI być dokładnie jedną z wartości: success, partial, failed.
- "success" = cel w pełni osiągnięty; "partial" = częściowo / z dużym tarciem;
  "failed" = cel nieosiągnięty.
- Pole "session" opisuje, jak sesja faktycznie się zakończyła (steps_taken,
  max_steps, ended_reason). Jeśli sesja urwała się po kilku krokach, a cel nie
  został nawet podjęty, oceń "failed", a w summary wyraźnie zaznacz, że sesja
  została przerwana przedwcześnie i dane z niej są ograniczone.
- blockers i wins: maksymalnie po 4 pozycje, krótkie i konkretne. Mogą być puste ([]).
- Pisz rzeczowo. Nie opisuj metodologii. Zwróć tylko poprawny JSON.\
"""

_VALID_OUTCOMES = {"success", "partial", "failed"}


def _coerce_reflection(data: Any, persona_name: str) -> dict:
    """Validate/normalize the LLM reflection payload into the dashboard shape."""
    d = data if isinstance(data, dict) else {}
    outcome = str(d.get("outcome", "")).strip().lower()
    if outcome not in _VALID_OUTCOMES:
        outcome = "partial"

    def _str_list(v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()][:4]
        return []

    return {
        "outcome": outcome,
        "summary": str(d.get("summary", "")).strip(),
        "blockers": _str_list(d.get("blockers")),
        "wins": _str_list(d.get("wins")),
        "priorityFix": str(d.get("priorityFix", "")).strip(),
    }


async def generate_objective_reflection(store: TraceStore) -> dict:
    """Call the LLM to produce a structured objective_reflection.json for one agent."""
    persona_card = store.read_persona_card() or {}
    basic = store.read_basic_info()
    heuristic_findings = store.read_heuristic_findings()
    first_impression = store.read_first_impression() or {}
    accessibility = store.read_accessibility_findings() or {}
    survey = store.read_survey_result() or {}
    scroll = store.read_scroll_summary() or {}
    form = store.read_form_funnel() or {}
    persona_name = persona_card.get("name", "Unknown")

    payload = {
        "persona_name": persona_name,
        "objective": persona_card.get("objective", basic.get("intent", "")),
        "demographics": persona_card.get("demographics", {}),
        "first_impression_score": first_impression.get("overall_score"),
        "first_impression_summary": first_impression.get("clarity_summary", ""),
        "heuristic_findings": [
            {
                "heuristic_id": f.get("heuristic_id"),
                "severity": f.get("severity"),
                "evidence": f.get("evidence", ""),
                "recommendation": f.get("recommendation", ""),
            }
            for f in heuristic_findings
        ],
        "accessibility_score": accessibility.get("overall_score"),
        "accessibility_summary": accessibility.get("summary", ""),
        "scroll_summary": scroll,
        "form_engagement": form,
        "survey_answers": survey.get("answers", []),
        "session": store.read_session_result() or {},
    }

    try:
        result = await async_chat(
            [
                {"role": "system", "content": _REFLECTION_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            json_mode=True,
            # "report" -> gemini-2.5-pro. This pass decides `outcome`
            # (success/partial/failed), which iso_metrics turns into
            # task_success and goal_state_reached — the headline effectiveness
            # signal — and which bias_audit and the dashboard verdict both read.
            # Nothing checks it (§4.2), so it is the single most consequential
            # judgement in the run and belongs on the audit tier.
            model="report",
        )
        parsed = json.loads(result)
        reflection = _coerce_reflection(parsed, persona_name)
    except Exception as exc:
        log.warning("[reflection] LLM call failed for %s: %s", store.trace_dir, exc)
        reflection = {
            "outcome": "partial",
            "summary": "",
            "blockers": [],
            "wins": [],
            "priorityFix": "",
        }

    store.write_objective_reflection(reflection)
    log.info("[reflection] Saved objective_reflection.json for %s", store.trace_dir)
    return reflection


# ---------------------------------------------------------------------------
# Aggregated report
# ---------------------------------------------------------------------------

# Sessions shorter than this carry no meaningful post-session survey signal.
MIN_VALID_SESSION_STEPS = 5


def _is_truncated_session(report: dict) -> bool:
    """True when the session ended after too few steps to trust its survey."""
    sr = report.get("session_result") or {}
    steps = sr.get("steps_taken")
    return isinstance(steps, int) and steps < MIN_VALID_SESSION_STEPS


def aggregate_statistics(reports: list[dict]) -> dict:
    """Compute statistics across all per-agent reports."""
    n = len(reports)
    if n == 0:
        return {}

    # First impression scores
    fi_scores = [
        r["first_impression"].get("overall_score")
        for r in reports
        if isinstance(r.get("first_impression"), dict)
        and r["first_impression"].get("overall_score") is not None
    ]

    fi_dimension_scores: dict[str, list[float]] = defaultdict(list)
    for r in reports:
        fi = r.get("first_impression") or {}
        scores = fi.get("scores") or {}
        for k, v in scores.items():
            if v is not None:
                fi_dimension_scores[k].append(float(v))

    # Heuristic findings aggregation — annotate each finding with its persona
    # so aggregated issues can point back to concrete evidence.
    all_findings: list[dict] = []
    for r in reports:
        trace_dir = str(r.get("trace_dir", ""))
        persona_dir = trace_dir.replace("\\", "/").rstrip("/").split("/")[-1] if trace_dir else ""
        m = re.search(r"^Persona:\s*(\S+)", r.get("persona", ""), re.MULTILINE | re.IGNORECASE)
        persona_name = m.group(1).rstrip(".,;:") if m else persona_dir
        for f in r.get("heuristic_findings") or []:
            if isinstance(f, dict):
                all_findings.append({**f, "_persona_dir": persona_dir, "_persona_name": persona_name})

    # Exclude harness-suspect findings (interaction failures mis-attributed to
    # the site) from the headline severity counts and per-heuristic grouping, so
    # they can't drive the critical/serious verdict or the score. They remain in
    # all_findings and are surfaced separately as "needs verification".
    confirmed_findings = [f for f in all_findings if not f.get("harness_suspect")]
    needs_verification = sum(1 for f in all_findings if f.get("harness_suspect"))
    severity_totals = Counter(f.get("severity") for f in confirmed_findings)

    # Group findings by heuristic_id
    by_heuristic: dict[int, list[dict]] = defaultdict(list)
    for f in confirmed_findings:
        by_heuristic[f.get("heuristic_id", 0)].append(f)

    def _example(f: dict) -> dict:
        screenshot = f.get("screenshot")
        return {
            "persona_dir": f.get("_persona_dir", ""),
            "persona_name": f.get("_persona_name", ""),
            "step": f.get("step"),
            "url": f.get("url", ""),
            # persona-relative → run-relative so report.html can embed it
            "screenshot": f"{f.get('_persona_dir')}/{screenshot}" if screenshot else None,
            "observation_quote": f.get("observation_quote", ""),
            "evidence": f.get("evidence", ""),
            "verified": bool(f.get("verified")),
        }

    heuristic_summary = []
    for h_id in sorted(by_heuristic):
        group = by_heuristic[h_id]
        freq = len(group)
        worst_severity = min(group, key=_severity_sort_key).get("severity", "drobny")
        verified_group = [f for f in group if f.get("verified")]
        examples = [_example(f) for f in (verified_group or group)[:3]]
        heuristic_summary.append({
            "heuristic_id": h_id,
            "heuristic_name": HEURISTIC_NAMES.get(h_id, ""),
            "frequency": freq,
            "sessions_affected": f"{freq} z {n} uczestników",
            "worst_severity": worst_severity,
            "top_evidence": group[0].get("evidence", ""),
            "top_recommendation": group[0].get("recommendation", ""),
            "examples": examples,
            "verified": bool(verified_group),
        })
    heuristic_summary.sort(key=lambda x: (-x["frequency"], SEVERITY_ORDER.get(x["worst_severity"], 99)))

    # Accessibility
    a11y_scores = [
        r["accessibility"].get("overall_score")
        for r in reports
        if isinstance(r.get("accessibility"), dict)
        and r["accessibility"].get("overall_score") is not None
    ]

    # Viewport — count sessions (not pages) with at least one overflow
    mobile_overflow = 0
    tablet_overflow = 0
    for r in reports:
        vp = r.get("viewport_findings") or []
        if any(v.get("viewport") == "mobile" and v.get("horizontal_overflow") for v in vp):
            mobile_overflow += 1
        if any(v.get("viewport") == "tablet" and v.get("horizontal_overflow") for v in vp):
            tablet_overflow += 1

    # Survey — average numeric answers; collect open-text answers.
    # Sessions that ended after only a few steps carry no real experience:
    # their survey answers are excluded from the averages (first-impression
    # scores stay in — those are formed on the first page and remain valid).
    survey_scores: dict[str, list[float]] = defaultdict(list)
    survey_open: dict[str, list[str]] = defaultdict(list)
    excluded_surveys: list[dict] = []
    for r in reports:
        if _is_truncated_session(r):
            sr = r.get("session_result") or {}
            excluded_surveys.append({
                "persona_dir": str(r.get("trace_dir", "")).replace("\\", "/").rstrip("/").split("/")[-1],
                "steps_taken": sr.get("steps_taken"),
                "reason": f"sesja przerwana po {sr.get('steps_taken')} krokach — ankieta wykluczona ze średnich",
            })
            continue
        survey = r.get("survey_result") or {}
        for a in survey.get("answers") or []:
            qid = a.get("question_id", "")
            val = a.get("value", "")
            try:
                survey_scores[qid].append(float(val))
            except (ValueError, TypeError):
                survey_open[qid].append(str(val))

    def _avg(lst: list) -> float | None:
        clean = [x for x in lst if x is not None]
        return round(statistics.mean(clean), 2) if clean else None

    return {
        "participant_count": n,
        "first_impression": {
            "avg_score": _avg(fi_scores),
            "dimension_avgs": {k: _avg(v) for k, v in fi_dimension_scores.items()},
        },
        "heuristic": {
            "total_findings": len(confirmed_findings),
            "severity_counts": {
                "krytyczny": severity_totals.get("krytyczny", 0),
                "poważny": severity_totals.get("poważny", 0),
                "drobny": severity_totals.get("drobny", 0),
                "needs_verification": needs_verification,
            },
            "by_heuristic": heuristic_summary,
        },
        "accessibility": {
            "avg_score": _avg(a11y_scores),
        },
        "viewport": {
            "mobile_overflow_sessions": mobile_overflow,
            "tablet_overflow_sessions": tablet_overflow,
        },
        "survey": {
            "avg_scores": {k: _avg(v) for k, v in survey_scores.items()},
            "open_answers": {k: v for k, v in survey_open.items()},
            "surveys_included": n - len(excluded_surveys),
            "excluded_surveys": excluded_surveys,
        },
    }


def _top_issues_text(stats: dict, threshold: float = 0.5) -> str:
    n = stats.get("participant_count", 1)
    cutoff = max(1, round(n * threshold))
    top = [
        h for h in stats["heuristic"]["by_heuristic"]
        if h["frequency"] >= cutoff
    ]
    if not top:
        return "(brak problemów w ≥50% sesji)"
    return "\n".join(
        f"- H{h['heuristic_id']} {h['heuristic_name']}: {h['sessions_affected']} — {h['top_evidence'][:120]}"
        for h in top[:5]
    )


def _aggregate_scroll(stores: list[TraceStore]) -> tuple[dict, str]:
    """Return (data_dict, plain_text_summary) for scroll depth across all sessions."""
    summaries = [s.read_scroll_summary() for s in stores]
    summaries = [s for s in summaries if s]

    max_depths = [s["max_depth_pct"] for s in summaries if s.get("max_depth_pct") is not None]
    max_pxs = [s["max_scroll_px"] for s in summaries if s.get("max_scroll_px") is not None]

    def _avg(lst: list) -> float | None:
        return round(sum(lst) / len(lst), 1) if lst else None

    data = {
        "sessions_with_depth_pct": len(max_depths),
        "avg_max_depth_pct": _avg(max_depths),
        "min_max_depth_pct": round(min(max_depths), 1) if max_depths else None,
        "max_max_depth_pct": round(max(max_depths), 1) if max_depths else None,
        "avg_max_scroll_px": _avg(max_pxs),
    }

    if max_depths:
        text = (
            f"avg max depth: {data['avg_max_depth_pct']}% "
            f"(range {data['min_max_depth_pct']}–{data['max_max_depth_pct']}%) "
            f"across {len(max_depths)} sessions"
        )
    elif max_pxs:
        text = f"avg max scroll: {_avg(max_pxs):.0f}px (depth % unavailable — old trace format)"
    else:
        text = "(no scroll data available)"

    return data, text


def _aggregate_links(stores: list[TraceStore]) -> tuple[dict, str]:
    """Return (data_dict, plain_text_summary) for broken links across all sessions."""
    all_broken: list[str] = []
    total_checked = 0
    sessions_with_broken = 0

    for store in stores:
        findings = store.read_link_findings()
        session_broken = set()
        for page_result in findings:
            checked = page_result.get("links_checked", 0)
            total_checked += checked
            for b in page_result.get("broken") or []:
                url = b.get("url", "")
                session_broken.add(url)
                if url not in all_broken:
                    all_broken.append(url)
        if session_broken:
            sessions_with_broken += 1

    data = {
        "total_links_checked": total_checked,
        "unique_broken_urls": len(all_broken),
        "sessions_with_broken_links": sessions_with_broken,
        "broken_urls": all_broken[:20],
    }

    if not total_checked:
        text = "(link check not available — run with new traces)"
    elif not all_broken:
        text = f"No broken links detected across {total_checked} links checked."
    else:
        text = (
            f"{len(all_broken)} unique broken link(s) found "
            f"in {sessions_with_broken}/{len(stores)} sessions "
            f"({total_checked} total links checked). "
            f"Broken: {', '.join(all_broken[:5])}"
            + (" …" if len(all_broken) > 5 else "")
        )

    return data, text


def _aggregate_forms(stores: list[TraceStore]) -> tuple[dict, str]:
    """Return (data_dict, plain_text_summary) for form funnel across all sessions."""
    engaged = 0
    submitted = 0
    total_fields: list[int] = []

    for store in stores:
        ff = store.read_form_funnel()
        if not ff:
            continue
        if ff.get("engaged_form"):
            engaged += 1
        if ff.get("submitted_form"):
            submitted += 1
        fields = ff.get("fields_filled", 0)
        total_fields.append(fields)

    n = len(stores)
    avg_fields = round(sum(total_fields) / len(total_fields), 1) if total_fields else None

    data = {
        "sessions_engaged_form": engaged,
        "sessions_submitted_form": submitted,
        "avg_fields_filled": avg_fields,
        "total_sessions": n,
    }

    if not total_fields:
        text = "(form funnel data not available — run with new traces)"
    else:
        text = (
            f"{engaged}/{n} sessions engaged with a form field; "
            f"{submitted}/{n} sessions reached a submit action; "
            f"avg fields filled per session: {avg_fields}"
        )

    return data, text


async def generate_aggregated_report(
    stores: list[TraceStore],
    task_description: str,
    website_url: str,
    site_context: str = "",
    analytics_context: str = "",
) -> dict:
    """
    Load all per-agent reports, compute statistics, call large LLM for narrative,
    save runs/aggregated_report.json + .md.
    """
    reports = [s.read_ux_report() for s in stores]
    reports = [r for r in reports if r]

    if not reports:
        log.warning("[reporter] No per-agent reports found for aggregation")
        return {}

    # Derive site_context / analytics_context from first store if not supplied by caller
    if not site_context and stores:
        site_context = stores[0].read_basic_info().get("site_context", "") or ""
    if not analytics_context and stores:
        analytics_context = stores[0].read_basic_info().get("analytics_context", "") or ""

    stats = aggregate_statistics(reports)
    n = stats["participant_count"]

    # Determine runs/ dir and read page speed (must happen before prompt build)
    runs_dir = stores[0].trace_dir.parent if stores else Path("runs")
    pagespeed_data = read_pagespeed(runs_dir)
    pagespeed_summary = format_pagespeed_summary(pagespeed_data)

    # Aggregate new data passes
    scroll_data, scroll_text = _aggregate_scroll(stores)
    link_data, link_text = _aggregate_links(stores)
    form_data, form_text = _aggregate_forms(stores)

    # Build LLM prompt
    prompt_template = _prompts.get("aggregate_synthesize")

    fi_stats = json.dumps(stats["first_impression"], ensure_ascii=False, indent=2)
    heuristic_summary = json.dumps(
        stats["heuristic"]["by_heuristic"][:10], ensure_ascii=False, indent=2
    )
    a11y_summary = json.dumps(stats["accessibility"], ensure_ascii=False, indent=2)
    vp_summary = json.dumps(stats["viewport"], ensure_ascii=False, indent=2)
    sc = stats["heuristic"]["severity_counts"]
    top_issues = _top_issues_text(stats)
    survey_summary = json.dumps(stats.get("survey", {}), ensure_ascii=False, indent=2)

    prompt = prompt_template.format(
        site_context=site_context,
        analytics_context=analytics_context or "(not provided)",
        participant_count=n,
        task_description=task_description,
        website_url=website_url,
        first_impression_stats=fi_stats,
        heuristic_summary=heuristic_summary,
        accessibility_summary=a11y_summary,
        viewport_summary=vp_summary,
        pagespeed_summary=pagespeed_summary,
        scroll_summary=scroll_text,
        link_summary=link_text,
        form_summary=form_text,
        critical_count=sc.get("krytyczny", 0),
        serious_count=sc.get("poważny", 0),
        minor_count=sc.get("drobny", 0),
        top_issues=top_issues,
        survey_summary=survey_summary,
    )

    messages = [{"role": "user", "content": prompt}]
    try:
        raw = await async_chat(messages, model="report", log=False)
        # extract JSON from LLM output
        import regex
        matches = regex.findall(r"\{(?:[^{}]|(?R))*\}", raw, regex.DOTALL)
        narrative = json.loads(matches[0]) if matches else {"raw": raw}
    except Exception as e:
        log.exception("[reporter] aggregate LLM call failed: %s", e)
        narrative = {"error": str(e)}

    _attach_issue_evidence(narrative, stats)

    try:
        survey_summaries = await _summarize_survey(reports)
    except Exception as e:
        log.exception("[reporter] survey summaries failed (non-fatal): %s", e)
        survey_summaries = []

    aggregated = {
        "statistics": stats,
        "narrative": narrative,
        "pagespeed": pagespeed_data,
        "scroll": scroll_data,
        "links": link_data,
        "forms": form_data,
        "task": task_description,
        "website": website_url,
        "survey_summaries": survey_summaries,
    }

    (runs_dir / "aggregated_report.json").write_text(
        json.dumps(aggregated, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md = _render_aggregated_md(aggregated)
    (runs_dir / "aggregated_report.md").write_text(md, encoding="utf-8")

    from .reporter_html import save_html_report
    save_html_report(aggregated, runs_dir)

    log.info("[reporter] Aggregated report saved to %s", runs_dir)
    return aggregated


async def _summarize_survey(reports: list[dict]) -> list[str]:
    """One Polish 'why agents answered this way' summary per survey question.

    Returns a list aligned to the questionnaire's question order (taken from the
    first persona's answers — all personas answer the same questionnaire).
    """
    per_question: dict[str, list[str]] = {}
    order: list[str] = []
    for r in reports:
        if _is_truncated_session(r):
            continue
        survey = r.get("survey_result") or {}
        for a in survey.get("answers") or []:
            qid = a.get("question_id")
            if not qid:
                continue
            if qid not in per_question:
                per_question[qid] = []
                order.append(qid)
            per_question[qid].append(
                f"odpowiedź: {a.get('value', '')} | uzasadnienie: {str(a.get('reason', ''))[:400]}"
            )

    if not order:
        return []

    payload = {qid: per_question[qid] for qid in order}
    system = (
        "You are a UX researcher. For EACH survey question (keyed by question id), "
        "synthesise WHY the agents answered the way they did, based on their stated "
        "reasoning. 2-4 sentences per question, in Polish. "
        'Respond with ONLY JSON: {"summaries": {"<question_id>": "<polish summary>", ...}}'
    )
    raw = await async_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        json_mode=True,
        log=False,
    )
    summaries = (json.loads(raw).get("summaries")) or {}
    return [str(summaries.get(qid, "")) for qid in order]


def _attach_issue_evidence(narrative: dict, stats: dict) -> None:
    """Deterministically join per-heuristic evidence examples onto narrative issues.

    The LLM writes the prose; evidence (screenshots, URLs, observation quotes)
    comes straight from the trace via aggregate_statistics — never from the LLM.
    Issues whose heuristic has no verified example are marked verified: False.
    """
    if not isinstance(narrative, dict):
        return
    by_h = {
        h.get("heuristic_id"): h
        for h in ((stats.get("heuristic") or {}).get("by_heuristic") or [])
    }
    for key in ("critical_issues", "serious_issues", "minor_issues"):
        for issue in narrative.get(key) or []:
            if not isinstance(issue, dict):
                continue
            group = by_h.get(issue.get("heuristic_id"))
            if group:
                issue["examples"] = group.get("examples", [])
                issue["verified"] = bool(group.get("verified"))
            else:
                issue["examples"] = []
                issue["verified"] = False


def _narrative_from_stats(stats: dict) -> dict:
    """Build a minimal narrative dict purely from statistics (no LLM needed)."""
    n = stats.get("participant_count", 0)
    fi = stats.get("first_impression") or {}
    fi_avg = fi.get("avg_score")
    dim = fi.get("dimension_avgs") or {}
    heur = stats.get("heuristic") or {}
    sc = heur.get("severity_counts") or {}
    by_h = heur.get("by_heuristic") or []
    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    vp = stats.get("viewport") or {}
    survey = stats.get("survey") or {}
    q3_avg = (survey.get("avg_scores") or {}).get("q3")

    serious = [h for h in by_h if h.get("worst_severity") == "poważny"]
    minor = [h for h in by_h if h.get("worst_severity") == "drobny"]

    exec_parts = [f"Badanie z udziałem {n} uczestników ujawniło {sc.get('poważny', 0)} poważnych i {sc.get('drobny', 0)} drobnych naruszeń heurystyk Nielsena."]
    if q3_avg is not None:
        exec_parts.append(f"Kluczową barierą był brak ścieżki zakupu online — uczestnicy ocenili prostotę dodania do koszyka na {q3_avg:.1f}/5.")
    if fi_avg is not None:
        exec_parts.append(f"Pierwsze wrażenie oceniono na {fi_avg:.2f}/5.")

    strengths = []
    if dim.get("trustworthiness", 0) >= 3.5:
        strengths.append(f"Wiarygodność serwisu oceniona na {dim['trustworthiness']:.1f}/5 — strona buduje zaufanie.")
    if dim.get("purpose_clarity", 0) >= 3.0:
        strengths.append(f"Cel serwisu jest rozpoznawalny przy pierwszym kontakcie (jasność celu: {dim['purpose_clarity']:.1f}/5).")

    def _to_issue(h: dict) -> dict:
        return {
            "heuristic_id": h.get("heuristic_id"),
            "heuristic_name": h.get("heuristic_name", ""),
            "frequency": h.get("sessions_affected", ""),
            "impact": h.get("top_evidence", "")[:200],
            "recommendation": h.get("top_recommendation", ""),
        }

    recs = []
    for i, h in enumerate(serious[:5], 1):
        recs.append({
            "priority": i,
            "area": h.get("heuristic_name", ""),
            "action": h.get("top_recommendation", ""),
            "expected_impact": "",
        })

    return {
        "executive_summary": " ".join(exec_parts),
        "overall_ux_score": round(fi_avg, 1) if fi_avg else None,
        "score_rationale": f"Ocena odzwierciedla wyniki testu pierwszego wrażenia ({fi_avg:.2f}/5) oraz dominację problemów poważnych ({sc.get('poważny', 0)}) i drobnych ({sc.get('drobny', 0)})." if fi_avg else "",
        "key_strengths": strengths or ["Brak wyraźnych mocnych stron możliwych do wyodrębnienia z danych."],
        "critical_issues": [],
        "serious_issues": [_to_issue(h) for h in serious],
        "minor_issues": [_to_issue(h) for h in minor],
        "accessibility_assessment": f"Średnia ocena dostępności: {a11y_avg}/5." if a11y_avg else "",
        "responsiveness_assessment": f"Sesje z overflowem tabletowym: {vp.get('tablet_overflow_sessions', 0)}/{n}. Brak overflowu mobilnego: {vp.get('mobile_overflow_sessions', 0)}/{n}.",
        "first_impression_assessment": f"Pierwsze wrażenie: {fi_avg:.2f}/5. Najsłabszy wymiar: wezwanie do działania ({dim.get('call_to_action', '—')}/5)." if fi_avg else "",
        "priority_recommendations": recs,
        "methodology_note": f"Badanie przeprowadzono przy użyciu agentów LLM symulujących rzeczywistych użytkowników. Każdy agent wykonał przydzielone zadanie. Ocena opiera się na heurystykach Nielsena, teście pierwszego wrażenia (5 sekund), analizie dostępności WCAG 2.1 oraz sprawdzeniu responsywności na urządzeniach mobilnych i tabletach.",
    }


def _render_aggregated_md(agg: dict) -> str:
    lines: list[str] = []
    stats = agg.get("statistics") or {}
    narr = agg.get("narrative") or {}
    # Fall back to stats-derived narrative when LLM call failed or returned empty
    if not narr or "error" in narr or not narr.get("executive_summary"):
        narr = _narrative_from_stats(stats)
        _attach_issue_evidence(narr, stats)
    n = stats.get("participant_count", 0)

    def h(level: int, text: str):
        lines.append(f"{'#' * level} {text}\n")

    def p(text: str):
        lines.append(f"{text}\n")

    def ul(items: list):
        for i in items:
            lines.append(f"- {i if isinstance(i, str) else json.dumps(i, ensure_ascii=False)}")
        lines.append("")

    h(1, f"Raport zbiorczy UX — {agg.get('website', '')}")
    p(f"**Zadanie:** {agg.get('task', '')}  |  **Liczba uczestników:** {n}")

    if narr.get("executive_summary"):
        h(2, "Podsumowanie wykonawcze")
        p(narr["executive_summary"])

    overall = narr.get("overall_ux_score")
    if overall is not None:
        p(f"**Ogólna ocena UX: {overall}/5** — {narr.get('score_rationale', '')}")

    # Key strengths
    strengths = narr.get("key_strengths") or []
    if strengths:
        h(2, "Mocne strony")
        ul(strengths)

    # Critical issues
    for sev_key, sev_label in [("critical_issues", "Problemy krytyczne"), ("serious_issues", "Problemy poważne"), ("minor_issues", "Problemy drobne")]:
        issues = narr.get(sev_key) or []
        if issues:
            h(2, sev_label)
            for issue in issues:
                h(3, f"H{issue.get('heuristic_id', '?')}: {issue.get('heuristic_name', '')}")
                if issue.get("verified") is False:
                    p("⚠ **Niezweryfikowane** — brak dowodu w zapisie sesji.")
                p(f"**Częstotliwość:** {issue.get('frequency', '')}")
                p(f"**Wpływ:** {issue.get('impact', '')}")
                p(f"**Rekomendacja:** {issue.get('recommendation', '')}")
                for ex in issue.get("examples") or []:
                    parts = [f"**Dowód:** {ex.get('url', '')}"]
                    if ex.get("persona_name"):
                        parts.append(f"(uczestnik {ex['persona_name']}, krok {ex.get('step')})")
                    if ex.get("screenshot"):
                        parts.append(f"— zrzut: `{ex['screenshot']}`")
                    p(" ".join(parts))
                    if ex.get("observation_quote"):
                        p(f"> {ex['observation_quote'][:300]}")

    # Assessments
    for key, label in [
        ("first_impression_assessment", "Pierwsze wrażenie"),
        ("accessibility_assessment", "Dostępność"),
        ("responsiveness_assessment", "Responsywność"),
    ]:
        val = narr.get(key)
        if val:
            h(2, label)
            p(val)

    # Priority recommendations
    recs = narr.get("priority_recommendations") or []
    if recs:
        h(2, "Priorytetowe rekomendacje")
        for rec in recs:
            h(3, f"{rec.get('priority', '?')}. {rec.get('area', '')}")
            p(rec.get("action", ""))
            expected = rec.get("expected_impact")
            if expected:
                p(f"*Oczekiwany efekt:* {expected}")

    # Survey scores
    survey_stats = stats.get("survey") or {}
    avg_scores = survey_stats.get("avg_scores") or {}
    open_answers = survey_stats.get("open_answers") or {}
    if avg_scores or open_answers:
        h(2, "Wyniki ankiety")
        _QUESTION_LABELS = {
            "q1": "Łatwość znalezienia produktu",
            "q2": "Przejrzystość i czytelność strony",
            "q3": "Łatwość sprawdzenia dostępności / znalezienia sklepu",
            "q4": "Sprawność działania strony",
        }
        for qid, avg in sorted(avg_scores.items()):
            label = _QUESTION_LABELS.get(qid, qid)
            avg_str = f"{avg:.2f}" if avg is not None else "—"
            p(f"**{label} ({qid}):** {avg_str}/5")
        for qid, answers in sorted(open_answers.items()):
            label = _QUESTION_LABELS.get(qid, qid)
            h(3, label)
            ul([f'„{a}"' for a in answers])

    # Page speed
    ps = agg.get("pagespeed")
    if ps and not ps.get("skipped"):
        h(2, "Szybkość strony (PageSpeed Insights)")
        for strategy in ("mobile", "desktop"):
            data = ps.get(strategy) or {}
            if data.get("error"):
                continue
            label = strategy.capitalize()
            score = data.get("performance_score", "—")
            perf_label = data.get("performance_label", "").upper()
            si = data.get("speed_index") or {}
            fcp = data.get("fcp") or {}
            lcp = data.get("lcp") or {}
            tbt = data.get("tbt") or {}
            cls_ = data.get("cls") or {}
            p(f"**{label}** — Performance score: **{score}/100** [{perf_label}]")
            p(f"| Metryka | Wartość | Status |")
            p(f"|---|---|---|")
            p(f"| Speed Index | {si.get('display','—')} | {si.get('status','').upper()} |")
            p(f"| LCP (Largest Contentful Paint) | {lcp.get('display','—')} | {lcp.get('status','').upper()} |")
            p(f"| FCP (First Contentful Paint) | {fcp.get('display','—')} | {fcp.get('status','').upper()} |")
            p(f"| TBT (Total Blocking Time) | {tbt.get('display','—')} | — |")
            p(f"| CLS (Cumulative Layout Shift) | {cls_.get('display','—')} | — |")

    # Scroll depth
    scroll = agg.get("scroll") or {}
    avg_max = scroll.get("avg_max_depth_pct")
    if avg_max is not None:
        h(2, "Głębokość przewijania")
        mn = scroll.get("min_max_depth_pct", "—")
        mx = scroll.get("max_max_depth_pct", "—")
        p(f"Średnia maks. głębokość przewijania: **{avg_max}%** (zakres {mn}–{mx}%)")
        if avg_max < 30:
            p("⚠ Uczestnicy rzadko przewijali poniżej 30% strony — treści below-the-fold mogą być pomijane.")

    # Broken links
    links = agg.get("links") or {}
    broken_urls = links.get("broken_urls") or []
    total_checked = links.get("total_links_checked", 0)
    if total_checked:
        h(2, "Uszkodzone linki")
        if broken_urls:
            p(f"Znaleziono **{len(broken_urls)}** uszkodzony(ch) link(ów) spośród {total_checked} sprawdzonych:")
            for u in broken_urls[:10]:
                lines.append(f"- {u}")
            lines.append("")
        else:
            p(f"Nie wykryto uszkodzonych linków (sprawdzono {total_checked} linków).")

    # Form funnel
    forms = agg.get("forms") or {}
    sessions_engaged = forms.get("sessions_engaged_form")
    sessions_submitted = forms.get("sessions_submitted_form")
    total_sessions = forms.get("total_sessions")
    if sessions_engaged is not None:
        h(2, "Lejek formularzy")
        p(f"Sesje, które wypełniły pole formularza: **{sessions_engaged}/{total_sessions}**")
        p(f"Sesje, które dotarły do akcji submit: **{sessions_submitted}/{total_sessions}**")
        avg_fields = forms.get("avg_fields_filled")
        if avg_fields is not None:
            p(f"Średnia liczba pól wypełnionych na sesję: **{avg_fields}**")

    # Statistics table
    h(2, "Statystyki zbiorcze")
    heur = stats.get("heuristic", {})
    sc = heur.get("severity_counts", {})
    p(f"Łączna liczba problemów: **{heur.get('total_findings', 0)}** "
      f"({sc.get('krytyczny', 0)} kryt. / {sc.get('poważny', 0)} poważnych / {sc.get('drobny', 0)} drobnych)")

    fi_avg = (stats.get("first_impression") or {}).get("avg_score")
    if fi_avg is not None:
        p(f"Średnia ocena pierwszego wrażenia: **{fi_avg}/5**")

    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    if a11y_avg is not None:
        p(f"Średnia ocena dostępności: **{a11y_avg}/5**")

    vp = stats.get("viewport") or {}
    p(f"Sesje z problemami responsywności: mobile {vp.get('mobile_overflow_sessions', 0)}/{n}, "
      f"tablet {vp.get('tablet_overflow_sessions', 0)}/{n}")

    # Methodology note
    mn = narr.get("methodology_note")
    if mn:
        h(2, "Nota metodologiczna")
        p(mn)

    return "\n".join(lines)
