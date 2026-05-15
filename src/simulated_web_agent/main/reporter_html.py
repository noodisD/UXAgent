"""
HTML report generation for the aggregated UX report.

generate_html_report(agg: dict) -> str   — returns a self-contained HTML string
save_html_report(agg, runs_dir)          — writes aggregated_report.html
"""

from __future__ import annotations

import html
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEVERITY_COLORS = {
    "poważny":   ("#fff3cd", "#856404", "#ffc107"),   # bg, text, border
    "drobny":    ("#d1ecf1", "#0c5460", "#17a2b8"),
    "krytyczny": ("#f8d7da", "#721c24", "#dc3545"),
}

SEVERITY_LABELS = {
    "poważny": "POWAŻNY",
    "drobny": "DROBNY",
    "krytyczny": "KRYTYCZNY",
}

QUESTION_LABELS = {
    "q1": "Łatwość znalezienia produktu",
    "q2": "Przejrzystość i czytelność",
    "q3": "Prostota dodania do koszyka",
    "q4": "Sprawność działania strony",
    "q5": "Największa trudność",
    "q6": "Czego nie udało się znaleźć",
}


def _esc(s: object) -> str:
    return html.escape(str(s or ""))


def _score_color(score: float | None) -> str:
    if score is None:
        return "#6c757d"
    if score >= 4.0:
        return "#28a745"
    if score >= 3.0:
        return "#ffc107"
    return "#dc3545"


def _score_bar(score: float | None, max_score: float = 5.0) -> str:
    if score is None:
        return ""
    pct = min(100, round(score / max_score * 100))
    color = _score_color(score)
    return f"""
        <div class="score-bar-wrap">
          <div class="score-bar" style="width:{pct}%; background:{color};"></div>
          <span class="score-label" style="color:{color};">{score:.2f}/5</span>
        </div>"""


def _severity_chip(sev: str) -> str:
    bg, fg, _ = SEVERITY_COLORS.get(sev, ("#e2e3e5", "#383d41", "#6c757d"))
    label = SEVERITY_LABELS.get(sev, sev.upper())
    return f'<span class="chip" style="background:{bg};color:{fg};">{label}</span>'


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  font-size: 15px;
  line-height: 1.6;
  color: #212529;
  background: #f8f9fa;
}

/* ── Layout ── */
.page { max-width: 960px; margin: 0 auto; padding: 40px 24px 80px; }

/* ── Header ── */
.report-header {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 60%, #0f3460 100%);
  color: #fff;
  border-radius: 12px;
  padding: 40px 48px;
  margin-bottom: 32px;
}
.report-header h1 { font-size: 28px; font-weight: 700; margin-bottom: 8px; }
.report-header .subtitle { font-size: 14px; opacity: .75; margin-bottom: 20px; }
.header-meta { display: flex; gap: 32px; flex-wrap: wrap; margin-top: 24px; }
.meta-item { text-align: center; }
.meta-item .val {
  font-size: 32px; font-weight: 800;
  display: block;
}
.meta-item .lbl { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; opacity: .7; }

/* ── Section ── */
.section { background: #fff; border-radius: 10px; padding: 28px 32px; margin-bottom: 24px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
.section h2 { font-size: 18px; font-weight: 700; margin-bottom: 16px; padding-bottom: 10px; border-bottom: 2px solid #f0f0f0; }
.section h3 { font-size: 15px; font-weight: 600; margin: 18px 0 6px; }

/* ── Score overview ── */
.score-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; }
.score-card {
  border-radius: 8px; padding: 16px 20px;
  border: 1px solid #e9ecef;
  text-align: center;
}
.score-card .big { font-size: 36px; font-weight: 800; }
.score-card .lbl { font-size: 12px; color: #6c757d; margin-top: 4px; }

/* ── Score bar ── */
.score-row { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.score-row .dim-label { min-width: 220px; font-size: 13px; color: #495057; }
.score-bar-wrap { flex: 1; display: flex; align-items: center; gap: 10px; }
.score-bar-wrap .score-bar { height: 10px; border-radius: 5px; transition: width .3s; min-width: 4px; }
.score-bar-wrap .score-label { font-size: 13px; font-weight: 600; white-space: nowrap; }

/* ── Chip ── */
.chip {
  display: inline-block; font-size: 11px; font-weight: 700;
  padding: 2px 8px; border-radius: 4px;
  letter-spacing: .04em; margin-right: 6px;
}

/* ── Issue card ── */
.issue-card {
  border-radius: 8px; padding: 16px 20px;
  margin-bottom: 14px;
  border-left: 4px solid;
}
.issue-card .issue-header { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 8px; }
.issue-card .issue-title { font-size: 14px; font-weight: 700; flex: 1; }
.issue-card .freq { font-size: 12px; color: #6c757d; margin-bottom: 6px; }
.issue-card .impact { font-size: 13px; margin-bottom: 8px; }
.issue-card .rec { font-size: 13px; background: rgba(255,255,255,.55); border-radius: 6px; padding: 8px 12px; }
.issue-card .rec-label { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; margin-bottom: 3px; opacity: .7; }

/* ── Survey ── */
.survey-grid { display: grid; gap: 12px; }
.quote-list { list-style: none; }
.quote-list li {
  background: #f8f9fa; border-left: 3px solid #dee2e6;
  border-radius: 0 6px 6px 0; padding: 10px 14px;
  font-size: 13px; font-style: italic; margin-bottom: 8px;
  color: #495057;
}

/* ── Recommendations ── */
.rec-list { counter-reset: rec; }
.rec-item { display: flex; gap: 16px; margin-bottom: 18px; }
.rec-num {
  background: #0f3460; color: #fff;
  border-radius: 50%; width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700; flex-shrink: 0; margin-top: 2px;
}
.rec-body .area { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
.rec-body .action { font-size: 13px; color: #495057; }
.rec-body .impact { font-size: 12px; color: #28a745; margin-top: 4px; font-style: italic; }

/* ── Strengths ── */
.strength-list { display: grid; gap: 10px; }
.strength-item {
  display: flex; align-items: flex-start; gap: 10px;
  background: #f0fff4; border-radius: 8px; padding: 10px 14px;
  font-size: 13px;
}
.strength-item::before { content: "✓"; color: #28a745; font-weight: 700; flex-shrink: 0; }

/* ── Viewport ── */
.viewport-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.vp-card { border-radius: 8px; padding: 14px 18px; border: 1px solid #e9ecef; }
.vp-card .vp-label { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; margin-bottom: 6px; color: #6c757d; }
.vp-ok { color: #28a745; font-weight: 700; }
.vp-warn { color: #dc3545; font-weight: 700; }

/* ── Footer ── */
.footer { font-size: 12px; color: #6c757d; text-align: center; margin-top: 40px; }

/* ── Print ── */
@media print {
  body { background: #fff; }
  .page { padding: 0; max-width: 100%; }
  .report-header { border-radius: 0; }
  .section { box-shadow: none; border: 1px solid #dee2e6; break-inside: avoid; }
  .issue-card { break-inside: avoid; }
}
"""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _section(title: str, body: str) -> str:
    return f'<div class="section"><h2>{_esc(title)}</h2>{body}</div>'


def _render_header(agg: dict, stats: dict) -> str:
    n = stats.get("participant_count", 0)
    fi_avg = (stats.get("first_impression") or {}).get("avg_score")
    heur = stats.get("heuristic") or {}
    sc = heur.get("severity_counts") or {}
    narr = agg.get("narrative") or {}
    overall = narr.get("overall_ux_score") or fi_avg

    score_color = _score_color(overall)
    website = _esc(agg.get("website", ""))
    task = _esc(agg.get("task", ""))

    return f"""
<div class="report-header">
  <h1>Raport UX — {website}</h1>
  <div class="subtitle">{task}</div>
  <div class="header-meta">
    <div class="meta-item">
      <span class="val" style="color:{score_color};">{overall if overall else "—"}/5</span>
      <span class="lbl">Ocena UX</span>
    </div>
    <div class="meta-item">
      <span class="val">{n}</span>
      <span class="lbl">Uczestników</span>
    </div>
    <div class="meta-item">
      <span class="val" style="color:#ffc107;">{sc.get("poważny", 0)}</span>
      <span class="lbl">Prob. poważnych</span>
    </div>
    <div class="meta-item">
      <span class="val" style="color:#17a2b8;">{sc.get("drobny", 0)}</span>
      <span class="lbl">Prob. drobnych</span>
    </div>
    <div class="meta-item">
      <span class="val">{(stats.get("first_impression") or {}).get("avg_score", "—")}/5</span>
      <span class="lbl">Pierwsze wrażenie</span>
    </div>
  </div>
</div>"""


def _render_executive(narr: dict) -> str:
    summary = _esc(narr.get("executive_summary", ""))
    rationale = _esc(narr.get("score_rationale", ""))
    if not summary:
        return ""
    body = f"<p>{summary}</p>"
    if rationale:
        body += f'<p style="margin-top:12px;font-size:13px;color:#6c757d;">{rationale}</p>'
    return _section("Podsumowanie wykonawcze", body)


def _render_strengths(narr: dict) -> str:
    items = narr.get("key_strengths") or []
    if not items:
        return ""
    rows = "".join(f'<div class="strength-item">{_esc(s)}</div>' for s in items)
    return _section("Mocne strony", f'<div class="strength-list">{rows}</div>')


def _render_issues_group(title: str, issues: list) -> str:
    if not issues:
        return ""
    cards = ""
    for issue in issues:
        sev = issue.get("severity") or ("poważny" if "poważn" in title.lower() else "drobny")
        bg, fg, border = SEVERITY_COLORS.get(sev, ("#f8f9fa", "#212529", "#dee2e6"))
        h_id = issue.get("heuristic_id", "?")
        h_name = _esc(issue.get("heuristic_name", ""))
        freq = _esc(issue.get("frequency", ""))
        impact = _esc(issue.get("impact", ""))
        rec = _esc(issue.get("recommendation", ""))
        cards += f"""
        <div class="issue-card" style="background:{bg};border-left-color:{border};">
          <div class="issue-header">
            {_severity_chip(sev)}
            <div class="issue-title" style="color:{fg};">H{h_id}: {h_name}</div>
          </div>
          <div class="freq">Częstotliwość: {freq}</div>
          <div class="impact">{impact}</div>
          <div class="rec"><div class="rec-label">Rekomendacja</div>{rec}</div>
        </div>"""
    return _section(title, cards)


def _render_all_issues(narr: dict) -> str:
    out = ""
    for key, title in [
        ("critical_issues", "Problemy krytyczne"),
        ("serious_issues", "Problemy poważne"),
        ("minor_issues", "Problemy drobne"),
    ]:
        issues = narr.get(key) or []
        for issue in issues:
            if key == "critical_issues":
                issue.setdefault("severity", "krytyczny")
            elif key == "serious_issues":
                issue.setdefault("severity", "poważny")
            else:
                issue.setdefault("severity", "drobny")
        out += _render_issues_group(title, issues)
    return out


def _render_first_impression(stats: dict, narr: dict) -> str:
    fi = stats.get("first_impression") or {}
    dim = fi.get("dimension_avgs") or {}
    assessment = _esc(narr.get("first_impression_assessment", ""))

    dim_labels = {
        "purpose_clarity": "Jasność celu",
        "navigation_visibility": "Widoczność nawigacji",
        "call_to_action": "Wezwanie do działania",
        "visual_hierarchy": "Hierarchia wizualna",
        "trustworthiness": "Wiarygodność",
    }
    rows = ""
    for key, label in dim_labels.items():
        score = dim.get(key)
        rows += f"""
        <div class="score-row">
          <div class="dim-label">{_esc(label)}</div>
          {_score_bar(score)}
        </div>"""

    body = rows
    if assessment:
        body += f'<p style="margin-top:14px;font-size:13px;color:#6c757d;">{assessment}</p>'
    return _section("Pierwsze wrażenie (test 5 sekund)", body)


def _render_survey(stats: dict) -> str:
    survey = stats.get("survey") or {}
    avg_scores = survey.get("avg_scores") or {}
    open_answers = survey.get("open_answers") or {}

    if not avg_scores and not open_answers:
        return ""

    rows = ""
    for qid in sorted(avg_scores):
        label = QUESTION_LABELS.get(qid, qid)
        score = avg_scores[qid]
        rows += f"""
        <div class="score-row">
          <div class="dim-label">{_esc(label)}</div>
          {_score_bar(score)}
        </div>"""

    quotes_html = ""
    for qid in sorted(open_answers):
        label = QUESTION_LABELS.get(qid, qid)
        answers = open_answers[qid]
        items = "".join(f"<li>&bdquo;{_esc(a)}&rdquo;</li>" for a in answers)
        quotes_html += f"<h3>{_esc(label)}</h3><ul class='quote-list'>{items}</ul>"

    body = f'<div class="survey-grid">{rows}</div>{quotes_html}'
    return _section("Wyniki ankiety", body)


def _render_accessibility(stats: dict, narr: dict) -> str:
    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    assessment = _esc(narr.get("accessibility_assessment", ""))
    color = _score_color(a11y_avg)
    body = f"""
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:14px;">
      <div style="font-size:40px;font-weight:800;color:{color};">{a11y_avg if a11y_avg else "—"}<span style="font-size:18px;">/5</span></div>
      <div style="font-size:13px;color:#6c757d;">Średnia ocena dostępności</div>
    </div>"""
    if assessment:
        body += f"<p>{assessment}</p>"
    return _section("Dostępność", body)


def _render_responsiveness(stats: dict, narr: dict) -> str:
    vp = stats.get("viewport") or {}
    n = stats.get("participant_count", 0)
    mobile_ok = vp.get("mobile_overflow_sessions", 0) == 0
    tablet_issues = vp.get("tablet_overflow_sessions", 0)
    assessment = _esc(narr.get("responsiveness_assessment", ""))

    mobile_label = "Brak overflowu" if mobile_ok else f"{vp.get('mobile_overflow_sessions', 0)}/{n} sesji z overflowem"
    tablet_label = f"{tablet_issues}/{n} sesji z overflowem" if tablet_issues else "Brak overflowu"

    body = f"""
    <div class="viewport-grid">
      <div class="vp-card">
        <div class="vp-label">Mobile</div>
        <div class="{'vp-ok' if mobile_ok else 'vp-warn'}">{mobile_label}</div>
      </div>
      <div class="vp-card">
        <div class="vp-label">Tablet</div>
        <div class="{'vp-ok' if not tablet_issues else 'vp-warn'}">{tablet_label}</div>
      </div>
    </div>"""
    if assessment:
        body += f'<p style="margin-top:14px;font-size:13px;color:#6c757d;">{assessment}</p>'
    return _section("Responsywność", body)


def _render_recommendations(narr: dict) -> str:
    recs = narr.get("priority_recommendations") or []
    if not recs:
        return ""
    items = ""
    for rec in recs:
        area = _esc(rec.get("area", ""))
        action = _esc(rec.get("action", ""))
        impact = _esc(rec.get("expected_impact", ""))
        priority = rec.get("priority", "")
        items += f"""
        <div class="rec-item">
          <div class="rec-num">{priority}</div>
          <div class="rec-body">
            <div class="area">{area}</div>
            <div class="action">{action}</div>
            {"" if not impact else f'<div class="impact">Oczekiwany efekt: {impact}</div>'}
          </div>
        </div>"""
    return _section("Priorytetowe rekomendacje", f'<div class="rec-list">{items}</div>')


def _render_stats_table(stats: dict) -> str:
    n = stats.get("participant_count", 0)
    heur = stats.get("heuristic") or {}
    sc = heur.get("severity_counts") or {}
    fi_avg = (stats.get("first_impression") or {}).get("avg_score")
    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    vp = stats.get("viewport") or {}
    survey = stats.get("survey") or {}
    q3 = (survey.get("avg_scores") or {}).get("q3")

    def row(label, value, color=None):
        style = f' style="color:{color};font-weight:700;"' if color else ""
        return f"<tr><td>{_esc(label)}</td><td{style}>{_esc(value)}</td></tr>"

    total = heur.get("total_findings", 0)
    rows = (
        row("Uczestnicy", n) +
        row("Łącznie problemów", total) +
        row("Problemy poważne", sc.get("poważny", 0), "#856404") +
        row("Problemy drobne", sc.get("drobny", 0), "#0c5460") +
        row("Pierwsze wrażenie (śr.)", f"{fi_avg}/5" if fi_avg else "—") +
        row("Dostępność (śr.)", f"{a11y_avg}/5" if a11y_avg else "—") +
        row("Prostota koszyka (q3)", f"{q3:.2f}/5" if q3 is not None else "—", _score_color(q3)) +
        row("Overflow mobile", f"{vp.get('mobile_overflow_sessions',0)}/{n} sesji") +
        row("Overflow tablet", f"{vp.get('tablet_overflow_sessions',0)}/{n} sesji")
    )
    table = f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <colgroup><col style="width:55%"><col style="width:45%"></colgroup>
      {rows}
    </table>
    <style>
      table tr td {{ padding:8px 12px; border-bottom:1px solid #f0f0f0; }}
      table tr:last-child td {{ border-bottom:none; }}
      table tr:nth-child(odd) td {{ background:#f8f9fa; }}
    </style>"""
    return _section("Statystyki zbiorcze", table)


def _render_methodology(narr: dict) -> str:
    note = _esc(narr.get("methodology_note", ""))
    if not note:
        return ""
    return _section("Nota metodologiczna",
                     f'<p style="font-size:13px;color:#6c757d;">{note}</p>')


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def generate_html_report(agg: dict) -> str:
    stats = agg.get("statistics") or {}
    narr = agg.get("narrative") or {}

    from .reporter import _narrative_from_stats  # avoid circular at module level
    if not narr or "error" in narr or not narr.get("executive_summary"):
        narr = _narrative_from_stats(stats)

    body = (
        _render_header(agg, stats) +
        '<div class="page">' +
        _render_executive(narr) +
        _render_strengths(narr) +
        _render_all_issues(narr) +
        _render_recommendations(narr) +
        _render_first_impression(stats, narr) +
        _render_survey(stats) +
        _render_accessibility(stats, narr) +
        _render_responsiveness(stats, narr) +
        _render_stats_table(stats) +
        _render_methodology(narr) +
        '</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Raport UX — {_esc(agg.get('website', ''))}</title>
  <style>{_CSS}</style>
</head>
<body>
{body}
<div class="footer">
  Wygenerowano automatycznie przez UXAgent &nbsp;·&nbsp; {_esc(agg.get('website', ''))}
</div>
</body>
</html>"""


def save_html_report(agg: dict, runs_dir: Path) -> Path:
    html_str = generate_html_report(agg)
    out = Path(runs_dir) / "aggregated_report.html"
    out.write_text(html_str, encoding="utf-8")
    return out
