"""
Report generation: per-agent and aggregated.

Per-agent:
  generate_per_agent_report(store) → trace_dir/ux_report.json + .md

Aggregated (called after ALL agents finish):
  generate_aggregated_report(stores, task, url) → runs/aggregated_report.json + .md
"""

import json
import logging
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..agent.gpt import async_chat
from ..agent.prompt_registry import registry as _prompts
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

    severity_counts = Counter(f.get("severity") for f in heuristic_findings)

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
        },
        "accessibility": accessibility,
        "viewport_findings": viewport,
        "survey_result": survey,
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
# Aggregated report
# ---------------------------------------------------------------------------

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

    # Heuristic findings aggregation
    all_findings: list[dict] = []
    for r in reports:
        all_findings.extend(r.get("heuristic_findings") or [])

    severity_totals = Counter(f.get("severity") for f in all_findings)

    # Group findings by heuristic_id
    by_heuristic: dict[int, list[dict]] = defaultdict(list)
    for f in all_findings:
        by_heuristic[f.get("heuristic_id", 0)].append(f)

    heuristic_summary = []
    for h_id in sorted(by_heuristic):
        group = by_heuristic[h_id]
        freq = len(group)
        worst_severity = min(group, key=_severity_sort_key).get("severity", "drobny")
        heuristic_summary.append({
            "heuristic_id": h_id,
            "heuristic_name": HEURISTIC_NAMES.get(h_id, ""),
            "frequency": freq,
            "sessions_affected": f"{freq} z {n} uczestników",
            "worst_severity": worst_severity,
            "top_evidence": group[0].get("evidence", ""),
            "top_recommendation": group[0].get("recommendation", ""),
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

    # Survey — average numeric answers; collect open-text answers
    survey_scores: dict[str, list[float]] = defaultdict(list)
    survey_open: dict[str, list[str]] = defaultdict(list)
    for r in reports:
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
            "total_findings": len(all_findings),
            "severity_counts": {
                "krytyczny": severity_totals.get("krytyczny", 0),
                "poważny": severity_totals.get("poważny", 0),
                "drobny": severity_totals.get("drobny", 0),
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


async def generate_aggregated_report(
    stores: list[TraceStore],
    task_description: str,
    website_url: str,
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

    stats = aggregate_statistics(reports)
    n = stats["participant_count"]

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
        participant_count=n,
        task_description=task_description,
        website_url=website_url,
        first_impression_stats=fi_stats,
        heuristic_summary=heuristic_summary,
        accessibility_summary=a11y_summary,
        viewport_summary=vp_summary,
        critical_count=sc.get("krytyczny", 0),
        serious_count=sc.get("poważny", 0),
        minor_count=sc.get("drobny", 0),
        top_issues=top_issues,
        survey_summary=survey_summary,
    )

    messages = [{"role": "user", "content": prompt}]
    try:
        raw = await async_chat(messages, model="large", log=False)
        # extract JSON from LLM output
        import regex
        matches = regex.findall(r"\{(?:[^{}]|(?R))*\}", raw, regex.DOTALL)
        narrative = json.loads(matches[0]) if matches else {"raw": raw}
    except Exception as e:
        log.exception("[reporter] aggregate LLM call failed: %s", e)
        narrative = {"error": str(e)}

    aggregated = {
        "statistics": stats,
        "narrative": narrative,
        "task": task_description,
        "website": website_url,
    }

    # Determine runs/ dir (parent of first trace_dir)
    runs_dir = stores[0].trace_dir.parent if stores else Path("runs")

    (runs_dir / "aggregated_report.json").write_text(
        json.dumps(aggregated, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md = _render_aggregated_md(aggregated)
    (runs_dir / "aggregated_report.md").write_text(md, encoding="utf-8")

    from .reporter_html import save_html_report
    save_html_report(aggregated, runs_dir)

    log.info("[reporter] Aggregated report saved to %s", runs_dir)
    return aggregated


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
                p(f"**Częstotliwość:** {issue.get('frequency', '')}")
                p(f"**Wpływ:** {issue.get('impact', '')}")
                p(f"**Rekomendacja:** {issue.get('recommendation', '')}")

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
            "q3": "Prostota dodania do koszyka",
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
