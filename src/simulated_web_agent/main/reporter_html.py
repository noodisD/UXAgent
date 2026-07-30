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
    # bg, text, border — Havas palette (report tier: supporting + accent allowed).
    # Critical owns Havas Red; serious steps down to Cadmium accent; minor uses a
    # supporting blue (blues complement red). All text AA on its own bg.
    "krytyczny": ("#FFDCDC", "#640000", "#E60000"),   # Light Pink / Maroon / Havas Red
    "poważny":   ("#FFF3CC", "#5C4A00", "#FFDC32"),   # Cadmium tint / dark amber
    "drobny":    ("#DCF6FA", "#145566", "#69EBF8"),   # Sky Blue tint / Petrol
}

SEVERITY_LABELS = {
    "poważny": "POWAŻNY",
    "drobny": "DROBNY",
    "krytyczny": "KRYTYCZNY",
}

QUESTION_LABELS = {
    "q1": "Łatwość znalezienia produktu",
    "q2": "Przejrzystość i czytelność",
    "q3": "Łatwość sprawdzenia dostępności / znalezienia sklepu",
    "q4": "Sprawność działania strony",
    "q5": "Największa trudność",
    "q6": "Czego nie udało się znaleźć",
}


def _esc(s: object) -> str:
    return html.escape(str(s or ""))


def _score_color(score: float | None) -> str:
    # Havas-aligned, AA on white: good = deep seafoam/teal, mid = dark cadmium,
    # low = Havas Red (used for large/bold numbers).
    if score is None:
        return "#6b6b6b"
    if score >= 4.0:
        return "#008573"
    if score >= 3.0:
        return "#B07A00"
    return "#E60000"


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

/* Havas brand tokens — Havas Red is purposeful accent only; neutrals dominant.
   Baikal is the brand face; Arial is the sanctioned fallback for HTML. */
:root {
  --havas-red: #E60000;
  --havas-maroon: #640000;
  --ink: #111111;
  --muted: #6b6b6b;
  --line: #e7e5e0;
  --page: #f5f4f2;
  --card: #ffffff;
}

body {
  font-family: 'Baikal', Arial, 'Helvetica Neue', Helvetica, sans-serif;
  font-size: 15px;
  line-height: 1.6;
  letter-spacing: -0.01em;
  color: var(--ink);
  background: var(--page);
}

/* ── Layout ── */
.page { max-width: 960px; margin: 0 auto; padding: 40px 24px 80px; }

/* ── Header ── */
.report-header {
  position: relative; overflow: hidden;
  background: #000; color: #fff;
  border-radius: 14px;
  padding: 46px 48px;
  margin-bottom: 28px;
}
/* 25° "Uplift" accent — the one signature angle, used sparingly. */
.report-header::after {
  content: ""; position: absolute; right: -50px; top: -46px;
  width: 240px; height: 30px; background: var(--havas-red);
  transform: rotate(-25deg);
}
.report-header h1 { font-size: 30px; font-weight: 700; letter-spacing: -0.02em;
  text-transform: uppercase; margin-bottom: 8px; position: relative; z-index: 1; }
.report-header .subtitle { font-size: 13.5px; color: rgba(255,255,255,.72);
  margin-bottom: 20px; position: relative; z-index: 1; }
.header-meta { display: flex; gap: 40px; flex-wrap: wrap; margin-top: 26px;
  position: relative; z-index: 1; }
.meta-item { text-align: left; }
.meta-item .val { font-size: 34px; font-weight: 800; letter-spacing: -0.02em; display: block; }
.meta-item .lbl { font-size: 10.5px; text-transform: uppercase; letter-spacing: .1em;
  color: rgba(255,255,255,.6); margin-top: 3px; }

/* ── Section ── */
.section { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 26px 30px; margin-bottom: 20px; }
.section h2 { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; margin-bottom: 18px; padding-left: 13px; position: relative; }
.section h2::before { content: ""; position: absolute; left: 0; top: 3px; bottom: 3px; width: 4px; border-radius: 2px; background: var(--havas-red); }
.section h3 { font-size: 14.5px; font-weight: 600; margin: 18px 0 6px; }

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
.unverified-badge { font-size: 11px; font-weight: 700; color: #8a6d00; background: #fff3cd; border: 1px solid #ffe08a; border-radius: 5px; padding: 2px 8px; white-space: nowrap; }
.evidence { display: flex; gap: 12px; align-items: flex-start; margin-top: 10px; background: rgba(255,255,255,.6); border-radius: 6px; padding: 10px; }
.ev-thumb { width: 140px; height: auto; border: 1px solid #dee2e6; border-radius: 4px; display: block; }
.ev-meta { flex: 1; min-width: 0; }
.ev-label { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; font-weight: 700; opacity: .65; margin-bottom: 3px; }
.ev-url { font-size: 12px; word-break: break-all; }
.ev-quote { font-size: 12px; font-style: italic; color: #495057; margin-top: 5px; }

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
  background: #111111; color: #fff;
  border-radius: 50%; width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700; flex-shrink: 0; margin-top: 2px;
}
.rec-body .area { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
.rec-body .action { font-size: 13px; color: #495057; }
.rec-body .impact { font-size: 12px; color: #008573; margin-top: 4px; font-style: italic; }

/* ── Strengths ── */
.strength-list { display: grid; gap: 10px; }
.strength-item {
  display: flex; align-items: flex-start; gap: 10px;
  background: #e6fbf6; border-radius: 8px; padding: 10px 14px;
  font-size: 13px;
}
.strength-item::before { content: "✓"; color: #008573; font-weight: 700; flex-shrink: 0; }

/* ── Viewport ── */
.viewport-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.vp-card { border-radius: 8px; padding: 14px 18px; border: 1px solid #e9ecef; }
.vp-card .vp-label { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; margin-bottom: 6px; color: #6c757d; }
.vp-ok { color: #008573; font-weight: 700; }
.vp-warn { color: #E60000; font-weight: 700; }

/* ── Page speed ── */
.ps-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.ps-card { border-radius: 8px; padding: 20px 24px; border: 1px solid #e9ecef; }
.ps-card .ps-device { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; font-weight: 700; color: #6c757d; margin-bottom: 8px; }
.ps-card .ps-score { font-size: 42px; font-weight: 800; line-height: 1; }
.ps-card .ps-label { font-size: 12px; font-weight: 600; margin-top: 2px; margin-bottom: 14px; }
.ps-metric { display: flex; justify-content: space-between; align-items: center; font-size: 12px; padding: 5px 0; border-bottom: 1px solid #f0f0f0; }
.ps-metric:last-child { border-bottom: none; }
.ps-metric .ps-mname { color: #495057; }
.ps-metric .ps-mval { font-weight: 600; }
.ps-status-good   { color: #008573; }
.ps-status-needs_improvement { color: #B07A00; }
.ps-status-poor   { color: #E60000; }
.ps-status-unknown { color: #6c757d; }

/* ── Survey (distribution + anchored scale) ── */
.sv-legend { font-size: 13px; color: #5b6169; line-height: 1.55; margin: 0 0 20px;
  padding: 12px 14px; background: #f6f7f9; border-radius: 8px; }
.sv-q { padding: 18px 0; border-bottom: 1px solid #eef0f3; }
.sv-q:last-child { border-bottom: none; padding-bottom: 4px; }
.sv-q-head { display: flex; justify-content: space-between; align-items: baseline;
  gap: 16px; margin-bottom: 14px; }
.sv-q-text { font-weight: 600; font-size: 15px; color: #1a1d21; line-height: 1.4; }
.sv-q-avg { font: 700 13px/1 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; }
.sv-hist { display: flex; gap: 6px; align-items: flex-end; height: 54px; }
.sv-col { flex: 1; display: flex; flex-direction: column; align-items: center;
  justify-content: flex-end; height: 100%; }
.sv-barwrap { flex: 1; width: 100%; display: flex; align-items: flex-end; }
.sv-bar { width: 100%; background: #e2e5ea; border-radius: 3px 3px 0 0; min-height: 2px; }
.sv-bar.filled { background: #E60000; }
.sv-cnt { font: 700 11px ui-monospace, monospace; color: #E60000; height: 15px; line-height: 15px; }
.sv-tick { font: 600 11px ui-monospace, monospace; color: #9aa1ab; margin-top: 3px; }
.sv-track { position: relative; height: 5px; border-radius: 3px; margin-top: 8px;
  background: linear-gradient(90deg, #f0b6b6, #f5e6a8 50%, #a9dcc0); }
.sv-marker { position: absolute; top: 50%; width: 13px; height: 13px; border-radius: 50%;
  background: #1a1d21; border: 2px solid #fff; box-shadow: 0 1px 3px rgba(0,0,0,.35);
  transform: translate(-50%, -50%); }
.sv-anchors { display: flex; justify-content: space-between; font-size: 11px;
  color: #8b929c; margin-top: 7px; }
.sv-n { font: 11px ui-monospace, monospace; color: #b7bcc4; margin-top: 8px; }
.sv-open .sv-q-tag { font: 600 10px ui-monospace, monospace; text-transform: uppercase;
  letter-spacing: .04em; color: #8b929c; border: 1px solid #e2e5ea; border-radius: 4px;
  padding: 3px 7px; white-space: nowrap; }
.sv-answers { list-style: none; margin: 4px 0 0; padding: 0; display: flex;
  flex-direction: column; gap: 7px; }
.sv-answers li { font-size: 13px; color: #40454c; background: #f6f7f9;
  border-left: 3px solid #cfd4db; padding: 9px 13px; border-radius: 0 6px 6px 0; line-height: 1.5; }

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
      <span class="val">{overall if overall else "—"}<span style="font-size:18px;font-weight:700;color:rgba(255,255,255,.45);">/5</span></span>
      <span class="lbl">Ocena UX</span>
    </div>
    <div class="meta-item">
      <span class="val">{n}</span>
      <span class="lbl">Uczestników</span>
    </div>
    <div class="meta-item">
      <span class="val" style="color:#B07A00;">{sc.get("poważny", 0)}</span>
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
        unverified_badge = (
            '<span class="unverified-badge">⚠ NIEZWERYFIKOWANE</span>'
            if issue.get("verified") is False
            else ""
        )
        evidence_html = ""
        for ex in issue.get("examples") or []:
            url = _esc(ex.get("url", ""))
            quote = _esc((ex.get("observation_quote") or "")[:300])
            who = _esc(ex.get("persona_name", ""))
            step = ex.get("step")
            shot = ex.get("screenshot")
            thumb = (
                f'<a href="{_esc(shot)}" target="_blank">'
                f'<img class="ev-thumb" src="{_esc(shot)}" alt="dowód: krok {step}"/></a>'
                if shot else ""
            )
            evidence_html += f"""
            <div class="evidence">
              {thumb}
              <div class="ev-meta">
                <div class="ev-label">Dowód — uczestnik {who}, krok {step}</div>
                <a class="ev-url" href="{url}" target="_blank">{url}</a>
                {f'<div class="ev-quote">„{quote}"</div>' if quote else ''}
              </div>
            </div>"""
        cards += f"""
        <div class="issue-card" style="background:{bg};border-left-color:{border};">
          <div class="issue-header">
            {_severity_chip(sev)}
            <div class="issue-title" style="color:{fg};">H{h_id}: {h_name}</div>
            {unverified_badge}
          </div>
          <div class="freq">Częstotliwość: {freq}</div>
          <div class="impact">{impact}</div>
          <div class="rec"><div class="rec-label">Rekomendacja</div>{rec}</div>
          {evidence_html}
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


_FI_DIM_LABELS = {
    "purpose_clarity": "Jasność celu",
    "navigation_visibility": "Widoczność nawigacji",
    "call_to_action": "Wezwanie do działania",
    "visual_hierarchy": "Hierarchia wizualna",
    "trustworthiness": "Wiarygodność",
}


def _render_first_impression(stats: dict, narr: dict) -> str:
    fi = stats.get("first_impression") or {}
    dist = fi.get("dimension_dist") or {}
    dim = fi.get("dimension_avgs") or {}
    assessment = _esc(narr.get("first_impression_assessment", ""))

    if dist:
        # Same distribution + anchored-scale technique as the survey: each
        # dimension shows how the participants split 1-5, the average, and what
        # low/high mean — reusing _render_scale_question for one consistent look.
        body = ['<p class="sv-legend">Ocena 5-sekundowa każdego wymiaru w skali '
                '1-5. Słupki pokazują rozkład ocen uczestników; ● to średnia.</p>']
        for key, label in _FI_DIM_LABELS.items():
            d = dist.get(key)
            if not d:
                continue
            body.append(_render_scale_question({
                "prompt": label, "kind": "scale", "min": 1, "max": 5,
                "low_label": "słabo", "high_label": "doskonale",
                "avg": d.get("avg"), "n": d.get("n", 0),
                "distribution": d.get("distribution") or [],
            }))
        body = "".join(body)
    else:
        # Legacy runs without per-dimension distribution: plain bars.
        body = "".join(
            f'<div class="score-row"><div class="dim-label">{_esc(label)}</div>'
            f'{_score_bar(dim.get(key))}</div>'
            for key, label in _FI_DIM_LABELS.items())

    if assessment:
        body += f'<p style="margin-top:16px;font-size:13px;color:var(--muted);">{assessment}</p>'
    return _section("Pierwsze wrażenie (test 5 sekund)", body)


def _scale_color(avg: float | None, lo: int, hi: int) -> str:
    """Colour a rated answer by where the average sits in its OWN range, so a
    1-7 SEQ is not judged against 1-5 thresholds."""
    if avg is None or hi <= lo:
        return "#6c757d"
    frac = (avg - lo) / (hi - lo)
    if frac >= 0.7:
        return "#008573"
    if frac >= 0.45:
        return "#d99a00"
    return "#E60000"


def _render_scale_question(q: dict) -> str:
    lo, hi = q["min"], q["max"]
    avg = q.get("avg")
    color = _scale_color(avg, lo, hi)
    dist = q.get("distribution") or []
    peak = max((d["count"] for d in dist), default=0) or 1
    cols = ""
    for d in dist:
        h = round(d["count"] / peak * 100)
        filled = " filled" if d["count"] else ""
        cols += (f'<div class="sv-col"><div class="sv-barwrap">'
                 f'<div class="sv-bar{filled}" style="height:{h}%"></div></div>'
                 f'<div class="sv-cnt">{d["count"] or ""}</div>'
                 f'<div class="sv-tick">{d["value"]}</div></div>')
    pct = round((avg - lo) / (hi - lo) * 100) if (avg is not None and hi > lo) else 0
    avgtxt = (f"{avg:.2f}".replace(".", ",")) if avg is not None else "—"
    return f"""
    <div class="sv-q">
      <div class="sv-q-head">
        <span class="sv-q-text">{_esc(q['prompt'])}</span>
        <span class="sv-q-avg" style="color:{color};">śr. {avgtxt} / {hi}</span>
      </div>
      <div class="sv-hist">{cols}</div>
      <div class="sv-track"><span class="sv-marker" style="left:{pct}%;"></span></div>
      <div class="sv-anchors"><span>{lo} · {_esc(q['low_label'])}</span>
        <span>{_esc(q['high_label'])} · {hi}</span></div>
      <div class="sv-n">n = {q.get('n', 0)}</div>
    </div>"""


def _render_open_question(q: dict) -> str:
    items = "".join(f"<li>&bdquo;{_esc(a)}&rdquo;</li>" for a in q.get("answers", []))
    return f"""
    <div class="sv-q sv-open">
      <div class="sv-q-head">
        <span class="sv-q-text">{_esc(q['prompt'])}</span>
        <span class="sv-q-tag">pytanie otwarte</span>
      </div>
      <ul class="sv-answers">{items}</ul>
    </div>"""


def _render_survey(stats: dict) -> str:
    survey = stats.get("survey") or {}
    questions = survey.get("questions") or []

    if not questions:
        # Legacy runs (no enriched questions): fall back to the old id + bar.
        avg_scores = survey.get("avg_scores") or {}
        if not avg_scores:
            return ""
        rows = "".join(
            f'<div class="score-row"><div class="dim-label">'
            f'{_esc(QUESTION_LABELS.get(qid, qid))}</div>{_score_bar(avg_scores[qid])}</div>'
            for qid in sorted(avg_scores))
        return _section("Wyniki ankiety", f'<div class="survey-grid">{rows}</div>')

    legend = ('<p class="sv-legend">Słupki pokazują, ilu uczestników wybrało daną '
              'wartość na skali; znacznik <b>●</b> to średnia. Kotwice skali (co '
              'oznacza wartość najniższa i najwyższa) są podane pod każdym pytaniem. '
              'Pytania otwarte cytują odpowiedzi uczestników.</p>')
    body = [legend]
    for q in questions:
        body.append(_render_open_question(q) if q.get("kind") == "open"
                    else _render_scale_question(q))
    return _section("Wyniki ankiety", "".join(body))


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


def _render_pagespeed(ps: dict | None) -> str:
    if not ps or ps.get("skipped") or ps.get("error"):
        return ""

    def _score_color_ps(score: int | None) -> str:
        if score is None:
            return "#6c757d"
        if score >= 90:
            return "#008573"
        if score >= 50:
            return "#B07A00"
        return "#E60000"

    def _status_class(status: str) -> str:
        return f"ps-status-{status}" if status else "ps-status-unknown"

    def _card(strategy: str) -> str:
        data = ps.get(strategy) or {}
        if data.get("error"):
            return f"""
            <div class="ps-card">
              <div class="ps-device">{strategy.capitalize()}</div>
              <div style="color:#E60000;font-size:13px;">Błąd: {_esc(data['error'])}</div>
            </div>"""

        score = data.get("performance_score")
        label_map = {"good": "Dobry", "needs_improvement": "Do poprawy", "poor": "Słaby"}
        label = label_map.get(data.get("performance_label", ""), "—")
        color = _score_color_ps(score)

        def metric(name: str, m: dict) -> str:
            status = m.get("status", "")
            css = _status_class(status)
            indicator = {"good": "✓", "needs_improvement": "⚠", "poor": "✗"}.get(status, "")
            return f"""
            <div class="ps-metric">
              <span class="ps-mname">{_esc(name)}</span>
              <span class="ps-mval {css}">{indicator} {_esc(m.get('display','—'))}</span>
            </div>"""

        si  = data.get("speed_index") or {}
        fcp = data.get("fcp") or {}
        lcp = data.get("lcp") or {}
        tbt = data.get("tbt") or {}
        cls_ = data.get("cls") or {}

        return f"""
        <div class="ps-card">
          <div class="ps-device">{strategy.capitalize()}</div>
          <div class="ps-score" style="color:{color};">{score if score is not None else '—'}</div>
          <div class="ps-label" style="color:{color};">{_esc(label)}</div>
          {metric("Speed Index", si)}
          {metric("LCP", lcp)}
          {metric("FCP", fcp)}
          {metric("TBT", {**tbt, "status": ""})}
          {metric("CLS", {**cls_, "status": ""})}
        </div>"""

    body = f'<div class="ps-grid">{_card("mobile")}{_card("desktop")}</div>'
    body += f'<p style="font-size:11px;color:#6c757d;margin-top:8px;">Źródło: Google PageSpeed Insights &nbsp;·&nbsp; {_esc(ps.get("url",""))}</p>'
    return _section("Szybkość strony (PageSpeed Insights)", body)


def _render_scroll_depth(scroll: dict | None) -> str:
    if not scroll:
        return ""
    avg = scroll.get("avg_max_depth_pct")
    if avg is None:
        return ""
    lo = scroll.get("min_max_depth_pct", "—")
    hi = scroll.get("max_max_depth_pct", "—")
    sessions = scroll.get("sessions_with_depth_pct", "—")
    pct = min(100, int(avg))
    color = "#008573" if pct >= 60 else ("#B07A00" if pct >= 30 else "#E60000")
    warning = ""
    if pct < 30:
        warning = '<p style="margin-top:10px;color:#E60000;font-size:13px;">⚠ Uczestnicy rzadko przewijali poniżej 30% strony — treści below-the-fold mogą być pomijane.</p>'
    body = f"""
    <div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap;margin-bottom:14px;">
      <div>
        <div style="font-size:42px;font-weight:800;color:{color};">{avg}%</div>
        <div style="font-size:12px;color:#6c757d;">Avg. maks. głębokość przewijania</div>
      </div>
      <div style="flex:1;min-width:180px;">
        <div style="background:#e9ecef;border-radius:6px;height:14px;overflow:hidden;">
          <div style="width:{pct}%;background:{color};height:100%;border-radius:6px;"></div>
        </div>
        <div style="font-size:12px;color:#6c757d;margin-top:4px;">zakres {lo}–{hi}% &nbsp;·&nbsp; {sessions} sesji</div>
      </div>
    </div>
    {warning}"""
    return _section("Głębokość przewijania", body)


def _render_broken_links(links: dict | None) -> str:
    if not links:
        return ""
    total = links.get("total_links_checked", 0)
    if not total:
        return ""
    broken_urls = links.get("broken_urls") or []
    sessions_broken = links.get("sessions_with_broken_links", 0)

    if not broken_urls:
        status_html = '<p style="color:#008573;font-weight:700;">✓ Nie wykryto uszkodzonych linków.</p>'
    else:
        items = "".join(
            f'<li style="padding:4px 0;font-size:12px;color:#721c24;"><code>{_esc(u)}</code></li>'
            for u in broken_urls[:10]
        )
        more = f"<li style='color:#6c757d;font-size:12px;'>…i {len(broken_urls) - 10} więcej</li>" if len(broken_urls) > 10 else ""
        status_html = f"""
        <p style="color:#E60000;font-weight:700;margin-bottom:8px;">
          ✗ {len(broken_urls)} uszkodzony(ch) link(ów) w {sessions_broken} sesjach
        </p>
        <ul style="list-style:none;padding-left:0;">{items}{more}</ul>"""

    body = f"""
    <p style="font-size:13px;color:#6c757d;margin-bottom:12px;">Sprawdzono {total} linków na odwiedzonych stronach.</p>
    {status_html}"""
    return _section("Uszkodzone linki", body)


def _render_form_funnel(forms: dict | None) -> str:
    if not forms:
        return ""
    n = forms.get("total_sessions", 0)
    engaged = forms.get("sessions_engaged_form", 0)
    submitted = forms.get("sessions_submitted_form", 0)
    avg_fields = forms.get("avg_fields_filled")

    if engaged is None:
        return ""

    def _step(label: str, count: int, color: str) -> str:
        pct = round(count / max(n, 1) * 100)
        return f"""
        <div style="margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px;">
            <span>{_esc(label)}</span>
            <span style="font-weight:700;color:{color};">{count}/{n} &nbsp;({pct}%)</span>
          </div>
          <div style="background:#e9ecef;border-radius:4px;height:10px;">
            <div style="width:{pct}%;background:{color};height:100%;border-radius:4px;"></div>
          </div>
        </div>"""

    body = (
        _step("Wszyscy uczestnicy", n, "#6c757d") +
        _step("Wypełnili pole formularza", engaged, "#17a2b8") +
        _step("Dotarli do akcji submit", submitted, "#008573")
    )
    if avg_fields is not None:
        body += f'<p style="font-size:13px;color:#6c757d;margin-top:12px;">Średnio {avg_fields} pól na sesję.</p>'
    return _section("Lejek formularzy", body)


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
    ps = agg.get("pagespeed")

    from .reporter import _narrative_from_stats  # avoid circular at module level
    if not narr or "error" in narr or not narr.get("executive_summary"):
        narr = _narrative_from_stats(stats)

    scroll = agg.get("scroll")
    links = agg.get("links")
    forms = agg.get("forms")

    body = (
        _render_header(agg, stats) +
        '<div class="page">' +
        _render_executive(narr) +
        _render_strengths(narr) +
        _render_all_issues(narr) +
        _render_recommendations(narr) +
        _render_pagespeed(ps) +
        _render_scroll_depth(scroll) +
        _render_broken_links(links) +
        _render_form_funnel(forms) +
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
