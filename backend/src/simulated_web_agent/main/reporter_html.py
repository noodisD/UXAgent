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

def _esc(s: object) -> str:
    """HTML-escape a value, rendering only None/missing as empty.

    `str(s or "")` treated every falsy value as absent, so a count of **0**
    rendered as a blank cell. In a UX report that is not a cosmetic slip: an
    empty "Problemy krytyczne" cell reads as "not measured", when what the
    pipeline actually found was zero critical problems — the opposite claim,
    and the more reassuring one. Caught when the critical row was added and
    printed nothing.

    Empty string stays empty; 0 and 0.0 render as themselves.
    """
    if s is None or s == "":
        return ""
    return html.escape(str(s))


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


def avg_text(value) -> str:
    """An average as every format prints it: two decimals, Polish comma.

    The survey printed "4,00", the first impression "3.07", accessibility "1"
    and the Word file "4.0" — four spellings of one kind of number, and the
    dashboard rounded the same average to "3.1".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value:.2f}".replace(".", ",")


from .obstacles import OBSTACLES_INTRO, obstacle_text  # noqa: E402,F401
from .recurring import INTRO as RECURRING_INTRO, theme_text  # noqa: E402,F401
from .textutil import (  # noqa: E402,F401  (shared with the Word renderer)
    clip_words, decimal_text, display_url, pl_plural, sessions_in,
)


def pct_text(value) -> str:
    """A percentage as the report prints it: one decimal, Polish comma ("22,0").

    The scroll depth printed "22.0%" beside averages printed "3,07" — the same
    page, two decimal separators.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—" if value in (None, "") else str(value)
    return f"{value:.1f}".replace(".", ",")


def evidence_quote(ex: dict, limit: int = 300) -> tuple[str, str]:
    """(label, text) for the quote under an evidence screenshot — one rule for
    every format.

    The page's own words where they can be found; otherwise the session's
    description of the page, labelled as such: it is written by the perception
    model, often in English, and is not a quotation from the site.
    """
    page_text = (ex.get("page_quote") or "").strip()
    text = clip_words(page_text or ex.get("observation_quote") or "", limit)
    label = "Tekst ze strony" if page_text else "Opis strony w zapisie sesji (automatyczny)"
    return label, text


def held_intro_text(k: int) -> str:
    """The lead-in to the held-back list.

    "{k} wniosków nie trafiło …" was printed for one item and for two. The count
    goes after the noun ("Wstrzymano wniosków: 2"), which agrees for any number,
    and the sentences after it are singular for one item and plural for more.
    """
    if k == 1:
        return ("Wstrzymano wniosków: 1. Zapis sesji nie pozwala go potwierdzić, więc nie "
                "trafił do ocen i liczników — nie znaczy to, że jest fałszywy. Wymaga "
                "krótkiej weryfikacji przez człowieka, opisanej poniżej.")
    return (f"Wstrzymano wniosków: {k}. Zapis sesji nie pozwala ich potwierdzić, więc nie "
            "trafiły do ocen i liczników — nie znaczy to, że są fałszywe. Każdy wymaga "
            "krótkiej weryfikacji przez człowieka, opisanej poniżej.")


def _score_bar(score: float | None, max_score: float = 5.0) -> str:
    if score is None:
        return ""
    pct = min(100, round(score / max_score * 100))
    color = _score_color(score)
    return f"""
        <div class="score-bar-wrap">
          <div class="score-bar" style="width:{pct}%; background:{color};"></div>
          <span class="score-label" style="color:{color};">{avg_text(score)}/5</span>
        </div>"""


def _severity_chip(sev: str) -> str:
    # The severity is the aggregate model's JSON, never type- or value-checked:
    # an int crashed the whole HTML and PDF build, and an unknown string was
    # printed raw, markup included.
    sev = sev if isinstance(sev, str) else str(sev or "")
    bg, fg, _ = SEVERITY_COLORS.get(sev, ("#e2e3e5", "#383d41", "#6c757d"))
    label = SEVERITY_LABELS.get(sev) or _esc(sev.upper())
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
/* A deliberate 3x2 grid rather than a wrapping row. There are six tiles, and
   at A4 width (which is what the PDF renders at) six columns cannot hold a
   label like "PROB. KRYTYCZNYCH" without wrapping mid-word — a flex row fit
   five and dropped the sixth onto a line by itself. Three columns give every
   label room and pair the severities on one row. */
.vp-na { color: #6c757d; font-weight: 600; }
.a11y-also { font-size: 12.5px; color: #3a3f47; margin: 4px 0; }
.obj-src { font-size: 11.5px; color: #6c757d; margin: 2px 0 6px; }
.header-note { position: relative; z-index: 1; margin-top: 18px; font-size: 12.5px; line-height: 1.5;
  color: rgba(255,255,255,.78); border-left: 2px solid rgba(255,255,255,.35); padding-left: 10px; }
.header-meta { display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 20px 32px; margin-top: 26px; position: relative; z-index: 1; }
@media (min-width: 900px) { .header-meta { grid-template-columns: repeat(6, 1fr); gap: 20px; } }
.meta-item { text-align: left; }
.meta-item .val { font-size: 30px; font-weight: 800; letter-spacing: -0.02em; display: block; }
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
.measured { font-size: 12.5px; font-weight: 600; color: #1f4e79; margin: 6px 0; }
.demoted { font-size: 12px; color: #6b6f76; font-style: italic; margin: 4px 0 8px; }
.sb-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.sb-table td { padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
.sb-kind { display: block; font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: #7a808a; }
.sb-basis { color: #5a5f66; font-size: 12px; }
.sb-amt { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; font-weight: 600; }
.sb-start td, .sb-sum td { color: #3a3f47; font-weight: 600; }
.sb-final td { border-top: 2px solid #3a3f47; border-bottom: none; font-weight: 700; font-size: 14px; }
.sb-left-h { font-weight: 600; font-size: 13px; margin: 16px 0 6px; }
.sb-left { font-size: 12.5px; color: #5a5f66; padding-left: 18px; }
.sb-rules { font-size: 11.5px; color: #7a808a; margin-top: 12px; }
.nv-card { border: 1px solid #ffe08a; background: #fffbea; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }
.nv-head { font-size: 13.5px; margin-bottom: 4px; }
.nv-meta { color: #7a808a; font-size: 12px; margin-left: 6px; }
.nv-claim { font-size: 13px; color: #3a3f47; font-style: italic; margin: 4px 0 6px; }
.nv-why, .nv-check { font-size: 12.5px; color: #5a5f66; margin: 2px 0; }
.ev-quote-l { font-style: normal; font-size: 11px; color: #7a808a; }
.st-card { display: flex; gap: 12px; border: 1px solid var(--line); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
.st-card .ev-thumb { width: 160px; flex: none; }
.st-who { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
.st-h { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #7a808a; margin-top: 4px; }
.st-body ul { margin: 2px 0 4px 16px; font-size: 12.5px; }
.st-ground-h { font-weight: 600; font-size: 13px; margin: 16px 0 4px; }
.obj-note { font-size: 12.5px; color: #5a5f66; background: #f6f7f9; border-left: 3px solid #c9ced6; border-radius: 4px; padding: 8px 12px; margin: 0 0 14px; }
.obj-caveat { font-size: 12px; color: #8a6d00; background: #fff3cd; border: 1px solid #ffe08a; border-radius: 6px; padding: 6px 10px; margin: 6px 0 2px; }
.unverified-badge { font-size: 11px; font-weight: 700; color: #8a6d00; background: #fff3cd; border: 1px solid #ffe08a; border-radius: 5px; padding: 2px 8px; white-space: nowrap; }
.other-obs { font-size: 12px; color: #6c757d; margin-top: 10px; }
.other-obs ul { margin: 4px 0 0 18px; padding: 0; }
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

/* ── First-impression screenshots ── */
.fi-shots { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
.fi-shot { margin: 0; width: 200px; }
.fi-shot img { width: 100%; height: auto; border: 1px solid var(--line); border-radius: 6px; display: block; }
.fi-shot figcaption { font-size: 11.5px; color: var(--muted); margin-top: 5px; text-align: center; }

/* ── Survey "why" synthesis ── */
.sv-why { display: flex; gap: 11px; align-items: flex-start; margin: 10px 0 20px; padding: 12px 14px; background: #fafbfc; border: 1px solid var(--line); border-radius: 9px; }
.sv-why-badge { flex-shrink: 0; width: 26px; height: 26px; border-radius: 7px; background: #ffdcdc; color: var(--havas-red); font-size: 10.5px; font-weight: 700; display: flex; align-items: center; justify-content: center; }
.sv-why-l { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin-bottom: 4px; }
.sv-why-t { font-size: 13px; line-height: 1.6; color: #3a3f47; }

/* ── Task outcomes ── */
.obj-tally { font-size: 13px; margin-bottom: 14px; }
.obj-card { border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; background: #fff; }
.obj-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.obj-who { font-weight: 700; font-size: 15px; }
.obj-outcome { font-size: 11.5px; font-weight: 700; border-radius: 20px; padding: 3px 11px; }
.obj-task { font-size: 12.5px; color: var(--muted); margin-bottom: 9px; font-style: italic; }
.obj-summary { font-size: 13.5px; line-height: 1.55; margin: 0 0 12px; }
.obj-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.obj-col-h { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }
.obj-list { margin: 0; padding-left: 16px; font-size: 12.5px; line-height: 1.5; color: #3a3f47; }
.obj-list li { margin-bottom: 4px; }
.obj-fix { margin-top: 12px; background: #fff6cc; border: 1px solid #ffee99; border-radius: 8px; padding: 10px 13px; font-size: 13px; }
.obj-fix-l { display: block; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: #8c791b; margin-bottom: 3px; }

/* ── Heuristic legend ── */
.hl-intro { font-size: 13px; color: var(--muted); line-height: 1.55; margin: 0 0 14px; }
.hl-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.hl-table td { padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
.hl-id { font-weight: 700; width: 44px; color: var(--muted); }
.hl-name { width: 46%; }
.hl-verdict { font-size: 12.5px; }

/* ── Score rationale ── */
.sr-dim { margin-bottom: 16px; }
.sr-dim-h { font-size: 13px; font-weight: 700; margin-bottom: 6px; }
.sr-dim ul { margin: 0; padding-left: 16px; font-size: 12.5px; line-height: 1.55; color: #3a3f47; }
.sr-dim li { margin-bottom: 5px; }
.sr-who { font-weight: 700; color: var(--ink); margin-right: 6px; }
.sr-fix { margin-top: 3px; padding-left: 10px; border-left: 2px solid #008573; color: #1f4f47; }

/* ── Accessibility detail ── */
.a11y-item { border: 1px solid var(--line); border-left-width: 3px; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }
.a11y-head { display: flex; align-items: center; gap: 9px; margin-bottom: 6px; flex-wrap: wrap; }
.a11y-sev { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; border-radius: 5px; padding: 3px 8px; }
.a11y-cat { font-weight: 700; font-size: 13.5px; }
.a11y-who { font-size: 11.5px; color: var(--muted); margin-left: auto; }
.a11y-detail { font-size: 13px; line-height: 1.5; color: #3a3f47; }
.a11y-fix { font-size: 13px; margin-top: 7px; }
.a11y-fix-l { font-weight: 700; color: var(--havas-red); margin-right: 5px; }
.a11y-where { font-size: 11.5px; color: var(--muted); margin-top: 6px; word-break: break-all; }

/* ── Per-device detail ── */
.dev-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
.dev-card { border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.dev-name { font-weight: 700; font-size: 14px; text-transform: capitalize; margin-bottom: 8px; }
.dev-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.dev-table td { padding: 5px 0; border-bottom: 1px solid var(--line); color: #3a3f47; }
.dev-v { text-align: right; font-weight: 700; color: var(--ink); white-space: nowrap; }
.dev-caveat { font-size: 12px; color: var(--muted); line-height: 1.5; margin: 12px 0 0; }

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

/* ── Print ──
   Also the PDF path: main/report_pdf.py renders this file through Chromium with
   emulate_media("print"), so these rules are what the downloadable report looks
   like — not a separate stylesheet that could drift from the screen one. */
@media print {
  body { background: #fff; }
  .page { padding: 0; max-width: 100%; }
  .report-header { border-radius: 0; }
  .section { box-shadow: none; border: 1px solid #dee2e6; break-inside: avoid; }
  .issue-card { break-inside: avoid; }
  /* Keep a unit of meaning on one page: a recommendation with its number, and a
     screenshot with the finding it is evidence for. */
  .rec-item, .evidence, .score-row, .quote-list li { break-inside: avoid; }
  /* A heading stranded at the foot of a page is worse than a shorter page. */
  h1, h2, h3 { break-after: avoid; }
  /* Evidence thumbs are sized for screen; A4 minus margins is narrower. */
  .ev-thumb { max-width: 100%; height: auto; }
  /* Long finding URLs must wrap rather than run off the page edge. */
  .ev-url, .section a { word-break: break-word; }
}
"""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _section(title: str, body: str) -> str:
    return f'<div class="section"><h2>{_esc(title)}</h2>{body}</div>'


def _issue_counts(narr: dict | None) -> dict:
    """Counts of the issue cards the report actually renders.

    The header and the statistics table used to count verified Nielsen findings
    only, while the cards beneath also carry the accessibility issues — so the
    review found "Łącznie problemów 2" above a body listing four. Counting the
    rendered lists (after evidence is joined and unproven WCAG claims demoted)
    means a number can never disagree with what is under it.
    """
    narr = narr or {}
    c = {k: len(narr.get(f"{k}_issues") or []) for k in ("critical", "serious", "minor")}
    c["total"] = sum(c.values())
    return c


def _render_header(agg: dict, stats: dict, narr: dict | None = None) -> str:
    n = stats.get("participant_count", 0)
    fi_avg = (stats.get("first_impression") or {}).get("avg_score")
    heur = stats.get("heuristic") or {}
    narr = narr if narr is not None else (agg.get("narrative") or {})
    counts = _issue_counts(narr)
    sc = {"krytyczny": counts["critical"], "poważny": counts["serious"],
          "drobny": counts["minor"]}
    overall = narr.get("overall_ux_score") or fi_avg
    overall_txt = (decimal_text(overall, 1) if isinstance(overall, (int, float))
                   and not isinstance(overall, bool) else (overall or "—"))

    score_color = _score_color(overall)
    website = _esc(agg.get("website", ""))
    task = _esc(agg.get("task", ""))

    return f"""
<div class="report-header">
  <h1>Raport UX — {website}</h1>
  <div class="subtitle">{task}</div>
  <div class="header-meta">
    <div class="meta-item">
      <span class="val">{overall_txt}<span style="font-size:18px;font-weight:700;color:rgba(255,255,255,.45);">/5</span></span>
      <span class="lbl">Ocena UX</span>
    </div>
    <div class="meta-item">
      <span class="val">{n}</span>
      <span class="lbl">Uczestników</span>
    </div>
    <div class="meta-item">
      <span class="val" style="color:#E60000;">{sc.get("krytyczny", 0)}</span>
      <span class="lbl">Prob. krytycznych</span>
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
      <span class="val">{_fi_header(stats)}</span>
      <span class="lbl">Pierwsze wrażenie</span>
    </div>
  </div>{_excluded_sessions_note(stats)}{_audit_failures_note(stats)}
</div>"""


def _fi_header(stats: dict) -> str:
    """The first-impression average, or "—": a present-but-null value printed "None/5"."""
    v = (stats.get("first_impression") or {}).get("avg_score")
    return f"{avg_text(v)}/5" if isinstance(v, (int, float)) else "—"


def _anything_observed(stats: dict) -> bool:
    """Did any session see the site at all? Absence of evidence is not a pass."""
    ex = [e for e in (stats.get("excluded_sessions") or []) if isinstance(e, dict)]
    n = int(stats.get("participant_count") or 0)
    if n and len(ex) >= n:
        return False
    # Not the accessibility average: archived runs scored 5/5 over nothing.
    fi = stats.get("first_impression") or {}
    heur = stats.get("heuristic") or {}
    return bool(heur.get("by_heuristic") or heur.get("excluded_by_heuristic")
                or fi.get("avg_score") is not None
                or ((stats.get("viewport") or {}).get("devices")))


def audit_failures_text(stats: dict) -> str:
    """Whose analyses failed, in one sentence — "" when none did."""
    rows = [r for r in (stats.get("audit_failures") or []) if isinstance(r, dict)]
    if not rows:
        return ""
    who = "; ".join(f"{r.get('persona') or '—'}: {', '.join(r.get('passes') or [])}" for r in rows)
    return (f"Nie wszystkie analizy się powiodły — {who}. W tych częściach ustalenia "
            "tych osób nie mogły trafić do raportu; brak problemów nie jest tu wynikiem.")


def _audit_failures_note(stats: dict) -> str:
    text = audit_failures_text(stats)
    return f'<div class="header-note">{_esc(text)}</div>' if text else ""


def _excluded_sessions_note(stats: dict) -> str:
    """Who was left out of the count, and why — so a smaller n is explained."""
    ex = [e for e in (stats.get("excluded_sessions") or []) if isinstance(e, dict)]
    if not ex:
        return ""
    total = int(stats.get("participant_count") or 0) + len(ex)
    who = "; ".join(f"{_esc(e.get('persona') or '—')}: {_esc(e.get('reason') or '')}" for e in ex)
    return (f'<div class="header-note">Pominięto {len(ex)} z {total} sesji, które nie '
            f'zobaczyły badanej strony, więc niczego o niej nie mówią — {who}.</div>')


def _render_executive(narr: dict) -> str:
    summary = _esc(narr.get("executive_summary", ""))
    rationale = _esc(narr.get("score_rationale", ""))
    if not summary:
        return ""
    body = f"<p>{summary}</p>"
    if rationale:
        body += f'<p style="margin-top:12px;font-size:13px;color:#6c757d;">{rationale}</p>'
    # "wykonawcze" reads as consultancy filler to the people who receive this;
    # the reviewer asked for it to go.
    return _section("Podsumowanie", body)


def _render_strengths(narr: dict, stats: dict | None = None) -> str:
    """Strengths, with what each participant actually praised and saw.

    Karolina asked to mark concretely what works, with screenshots "żeby było
    wiadomo o czym mówię". The section was a bare list of the aggregate model's
    sentences with no source. Each participant's own first-impression strengths
    (judged on the landing screen, shown beside them) and what went well during
    the task now sit underneath, so every summary line can be traced to who said
    it and what they were looking at.
    """
    items = narr.get("key_strengths") or []
    stats = stats or {}
    fi = stats.get("first_impression") or {}
    by_persona: dict[str, list[str]] = {}
    for s in fi.get("strengths") or []:
        if isinstance(s, dict) and s.get("persona") and s.get("text"):
            by_persona.setdefault(s["persona"], []).append(s["text"])
    wins = {r.get("persona"): [w for w in (r.get("wins") or []) if str(w).strip()]
            for r in (stats.get("objective_reflections") or []) if isinstance(r, dict)}
    shots = {s.get("persona"): s.get("screenshot")
             for s in (fi.get("screenshots") or []) if isinstance(s, dict)}
    if not items and not by_persona and not any(wins.values()):
        return ""

    rows = "".join(f'<div class="strength-item">{_esc(s)}</div>' for s in items)
    cards = ""
    for persona in sorted(set(by_persona) | {p for p, w in wins.items() if w and p}):
        shot = shots.get(persona)
        thumb = (f'<a href="{_esc(shot)}" target="_blank"><img class="ev-thumb" src="{_esc(shot)}" '
                 f'alt="ekran startowy — {_esc(persona)}"/></a>') if shot else ""
        fi_list = "".join(f"<li>{_esc(t)}</li>" for t in by_persona.get(persona, [])[:3])
        win_list = "".join(f"<li>{_esc(t)}</li>" for t in wins.get(persona, [])[:3])
        cards += f"""
        <div class="st-card">
          {thumb}
          <div class="st-body"><div class="st-who">{_esc(persona)}</div>
            {f'<div class="st-h">Na ekranie startowym</div><ul>{fi_list}</ul>' if fi_list else ''}
            {f'<div class="st-h">W trakcie zadania</div><ul>{win_list}</ul>' if win_list else ''}
          </div>
        </div>"""
    grounding = (
        '<div class="st-ground-h">Na czym to się opiera</div>'
        '<p class="sb-rules">Słowa uczestników badania i ekran, który oceniali. To ich '
        'opinie, nie pomiar — zbieżność między uczestnikami jest tu najmocniejszym '
        'sygnałem.</p>' + cards) if cards else ""
    return _section("Mocne strony",
                    f'<div class="strength-list">{rows}</div>{grounding}')


def _render_issues_group(title: str, issues: list) -> str:
    if not issues:
        return ""
    cards = ""
    for issue in issues:
        # _render_all_issues setdefaults `severity` on every issue before
        # calling this, so the old `or (guess from the group title)` fallback
        # was unreachable — and it could only ever produce poważny/drobny, so
        # had it run on a critical group it would have mislabelled it.
        sev = issue.get("severity", "drobny")
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
        # WCAG issues have no Nielsen heuristic. "HNone: Dostępność (WCAG)" was
        # printed as the title of both in the hyundai run.
        title_prefix = f"H{h_id}: " if isinstance(h_id, int) else ""

        # The measured denominator, where one exists. Without it the reader
        # sees "images lack alt text" and cannot tell 5 icons from every car.
        measured = issue.get("measured") or {}
        measured_html = ""
        if measured.get("images_total"):
            from .scoring import alt_text_measured_text
            text = measured.get("text") or alt_text_measured_text(measured)
            measured_html = f'<div class="measured">{_esc(text)}</div>'
        elif measured.get("basis"):
            measured_html = f'<div class="measured">Zmierzono: {_esc(measured["basis"])}.</div>'
        demotion_html = ""
        reason = issue.get("demotion_reason")
        if reason:
            was = {"critical_issues": "krytyczne", "serious_issues": "poważne"}.get(
                issue.get("demoted_from"), "wyższej kategorii")
            why = ("pomiar pokazuje, że dotyczy to niewielkiej części elementów"
                   if reason == "measured_share"
                   else "brak dowodu w zapisie sesji")
            demotion_html = (f'<div class="demoted">Przeniesiono z kategorii '
                             f'„{was}”: {why}.</div>')

        evidence_html = ""
        for ex in issue.get("examples") or []:
            url = _esc(ex.get("url", ""))
            # The page's own words where they can be found; otherwise the
            # session's description of the page, labelled as such — it is
            # written by the perception model, often in English, and is not a
            # quotation from the site.
            quote_label, quote_text = evidence_quote(ex)
            quote = _esc(quote_text)
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
                {f'<div class="ev-quote"><span class="ev-quote-l">{quote_label}:</span> „{quote}”</div>' if quote else ''}
              </div>
            </div>"""
        others = [o for o in (issue.get("other_observations") or []) if isinstance(o, dict)]
        if others:
            lines = "".join(
                f'<li>{_esc(o.get("persona_name", ""))}, krok {o.get("step")} — '
                f'„{_esc(clip_words(o.get("evidence", ""), 160))}”</li>' for o in others)
            evidence_html += (
                '<div class="other-obs"><b>Inne obserwacje w tej heurystyce</b> — dotyczą innej '
                f'wady niż opisana powyżej, więc bez zrzutu ekranu:<ul>{lines}</ul></div>')
        cards += f"""
        <div class="issue-card" style="background:{bg};border-left-color:{border};">
          <div class="issue-header">
            {_severity_chip(sev)}
            <div class="issue-title" style="color:{fg};">{title_prefix}{h_name}</div>
            {unverified_badge}
          </div>
          <div class="freq">Częstotliwość: {freq}</div>
          <div class="impact">{impact}</div>
          {measured_html}
          {demotion_html}
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

    # The screen the five-second scores were actually formed on. Without it the
    # section is numbers about a page the reader has never seen.
    shots = ""
    for s in (fi.get("screenshots") or [])[:4]:
        path = s.get("screenshot")
        if not path:
            continue
        score = f" · {avg_text(s['score'])}/5" if s.get("score") is not None else ""
        shots += (f'<figure class="fi-shot"><a href="{_esc(path)}" target="_blank">'
                  f'<img src="{_esc(path)}" alt="pierwszy ekran — {_esc(s.get("persona") or "")}"/></a>'
                  f'<figcaption>{_esc(s.get("persona") or "")}{score}</figcaption></figure>')
    shots = f'<div class="fi-shots">{shots}</div>' if shots else ""

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
    return _section("Pierwsze wrażenie (test 5 sekund)", shots + body)


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
    avgtxt = avg_text(avg)
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
      <div class="sv-n">n = {q.get('n', 0)}{_na_note(q)}</div>
    </div>"""


def _na_note(q: dict) -> str:
    """How many participants said they could not answer, beside n.

    Their placeholder "3"s used to sit in the average as neutral ratings, so a
    question nobody could really answer looked like a lukewarm result.
    """
    na = int(q.get("not_assessable") or 0)
    return (f" · {na} bez oceny (pytanie nie dotyczyło przebiegu sesji)" if na else "")


def _render_not_assessable_question(q: dict) -> str:
    return f"""
    <div class="sv-q">
      <div class="sv-q-head"><span class="sv-q-text">{_esc(q.get('prompt', ''))}</span>
        <span class="sv-q-avg" style="color:#6c757d;">brak ocen</span></div>
      <div class="sv-n">Żaden uczestnik nie mógł odpowiedzieć ({int(q.get('not_assessable') or 0)}):
        pytanie dotyczy etapu, do którego sesje nie dotarły.</div>
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


def _render_survey(stats: dict, summaries: dict | None = None) -> str:
    survey = stats.get("survey") or {}
    questions = survey.get("questions") or []

    if not questions:
        # Legacy runs (no enriched questions): fall back to the old id + bar.
        avg_scores = survey.get("avg_scores") or {}
        if not avg_scores:
            return ""
        rows = "".join(
            f'<div class="score-row"><div class="dim-label">'
            f'{_esc(qid)}</div>{_score_bar(avg_scores[qid])}</div>'
            for qid in sorted(avg_scores))
        return _section("Wyniki ankiety", f'<div class="survey-grid">{rows}</div>')

    legend = ('<p class="sv-legend">Słupki pokazują, ilu uczestników wybrało daną '
              'wartość na skali; znacznik <b>●</b> to średnia. Kotwice skali (co '
              'oznacza wartość najniższa i najwyższa) są podane pod każdym pytaniem. '
              'Pytania otwarte cytują odpowiedzi uczestników.</p>')
    body = [legend]
    for q in questions:
        body.append(_render_open_question(q) if q.get("kind") == "open"
                    else _render_not_assessable_question(q) if q.get("kind") == "not_assessable"
                    else _render_scale_question(q))
        # Why the participants answered that way — one synthesis per question,
        # generated at report time and, until now, never rendered. A bar chart
        # says three people scored navigation 3/5; this says what made them.
        # Joined by question id, not position: the question list is
        # questionnaire-ordered and omits questions nobody answered, so an
        # index join silently shifts every later summary onto the wrong one.
        why = (summaries or {}).get(q.get("id"))
        if str(why or "").strip():
            body.append(
                '<div class="sv-why"><span class="sv-why-badge">AI</span>'
                '<div><div class="sv-why-l">Dlaczego tak odpowiadali</div>'
                f'<div class="sv-why-t">{_esc(why)}</div></div></div>'
            )
    return _section("Wyniki ankiety", "".join(body))


def _render_accessibility(stats: dict, narr: dict) -> str:
    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    assessment = _esc(narr.get("accessibility_assessment", ""))
    color = _score_color(a11y_avg)
    body = f"""
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:14px;">
      <div style="font-size:40px;font-weight:800;color:{color};">{avg_text(a11y_avg)}<span style="font-size:18px;">/5</span></div>
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
    mobile_cls = "vp-ok" if mobile_ok else "vp-warn"
    tablet_cls = "vp-ok" if not tablet_issues else "vp-warn"
    # A zero overflow count on no scanned page is not a pass: both tmobile
    # sessions never loaded, and the section still said "Brak overflowu" in
    # green. Reports without the per-device list keep the old reading.
    if "devices" in vp:
        scanned = {d.get("viewport") for d in (vp.get("devices") or [])
                   if isinstance(d, dict) and d.get("pages_scanned")}
        if "mobile" not in scanned:
            mobile_label, mobile_cls = "Nie zmierzono", "vp-na"
        if "tablet" not in scanned:
            tablet_label, tablet_cls = "Nie zmierzono", "vp-na"

    body = f"""
    <div class="viewport-grid">
      <div class="vp-card">
        <div class="vp-label">Mobile</div>
        <div class="{mobile_cls}">{mobile_label}</div>
      </div>
      <div class="vp-card">
        <div class="vp-label">Tablet</div>
        <div class="{tablet_cls}">{tablet_label}</div>
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
        # Model output like the rest of the item, so escaped like it.
        priority = _esc(rec.get("priority", ""))
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


def pagespeed_error_text(data: dict) -> str:
    """A PageSpeed call that failed, in plain Polish — shared by HTML and Word."""
    err = str((data or {}).get("error") or "").lower()
    why = ("usługa Google PageSpeed nie odpowiedziała w czasie"
           if "timed out" in err or "timeout" in err
           else "usługa Google PageSpeed zwróciła błąd")
    return f"Pomiar nie powiódł się — {why}. To błąd pomiaru, nie wada strony; warto go powtórzyć."


PAGESPEED_LABELS = {"good": "Dobry", "needs_improvement": "Do poprawy", "poor": "Słaby"}


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
            # Plain language, in grey. The report printed the raw exception in
            # red — "Błąd: The read operation timed out" — which reads as a
            # fault on the site, in English, in a Polish client document. It is
            # a measurement that did not complete.
            return f"""
            <div class="ps-card">
              <div class="ps-device">{strategy.capitalize()}</div>
              <div style="color:#6c757d;font-size:13px;">{_esc(pagespeed_error_text(data))}</div>
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


_SCROLL_METHOD = (
    "Jak to mierzymy: przy każdym kroku uczestnika zapisywana jest pozycja "
    "przewinięcia okna przeglądarki — po przewinięciu do elementu, którego krok "
    "dotyczy — i wysokość strony; wynik to odsetek strony, do którego uczestnik "
    "dotarł. Pomijane są zapisy, które nie odzwierciedlają zachowania uczestnika: "
    "sprzed pierwszej akcji oraz z dopiero co wczytanej strony, na której krok "
    "nie dotyczył żadnego elementu. Są to zachowania "
    "symulowanych uczestników badania, a nie rzeczywistych użytkowników, i dane "
    "nie pochodzą z Google PageSpeed."
)


def scroll_coverage_text(scroll: dict) -> str:
    """How many steps the scroll figure rests on — shared by HTML and Word."""
    measured = int((scroll or {}).get("steps_measured") or 0)
    unmeasurable = int((scroll or {}).get("steps_unmeasurable") or 0)
    if not (measured or unmeasurable):
        return ""
    text = (f"Zmierzono na {measured} z {measured + unmeasurable} kroków. "
            if unmeasurable else f"Zmierzono na {measured} krokach. ")
    if unmeasurable:
        text += ("Na pozostałych strona przewija się wewnątrz własnych "
                 "paneli (jak konfigurator), czego ta metoda nie widzi.")
    return text


SCROLL_LOW_NOTE = ("Uczestnicy badania rzadko przewijali poniżej 30% strony. Warto "
                   "sprawdzić, czy ważne treści nie leżą zbyt nisko.")
SCROLL_UNMEASURABLE = ("Nie do zmierzenia na tej stronie — strona nie przewija się jako "
                       "całość, a treść przesuwa się wewnątrz paneli.")


def _render_scroll_depth(scroll: dict | None) -> str:
    """Scroll depth, with the answer to "how does the AI know?" on the page.

    The first external reviewer asked exactly that, and the section answered
    with a bare percentage placed under the PageSpeed source line. It also
    printed the wrong number on app-style pages: the Hyundai figure was the
    landing position recorded before any participant acted, identical in all
    three sessions. The method, the steps it rests on and what it cannot see
    are now stated where the number is.
    """
    if not scroll:
        return ""
    avg = scroll.get("avg_max_depth_pct")
    unmeasurable = int(scroll.get("steps_unmeasurable") or 0)
    coverage = scroll_coverage_text(scroll)
    method = f'<p class="sb-rules">{_esc(coverage)} {_esc(_SCROLL_METHOD)}</p>'
    if avg is None:
        if not unmeasurable:
            return ""
        return _section("Głębokość przewijania", (
            f'<p style="font-size:14px;">{_esc(SCROLL_UNMEASURABLE)}</p>'
            + method))
    lo = pct_text(scroll.get("min_max_depth_pct", "—"))
    hi = pct_text(scroll.get("max_max_depth_pct", "—"))
    k = scroll.get("sessions_with_depth_pct")
    sessions = f"{k} {pl_plural(k, 'sesja', 'sesje', 'sesji')}" if isinstance(k, int) else "— sesji"
    pct = min(100, int(avg))
    color = "#008573" if pct >= 60 else ("#B07A00" if pct >= 30 else "#E60000")
    warning = ""
    if pct < 30:
        # Participant behaviour, not a site verdict — worded as such.
        warning = ('<p style="margin-top:10px;color:#6b6f76;font-size:13px;">'
                   f'{_esc(SCROLL_LOW_NOTE)}</p>')
    body = f"""
    <div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap;margin-bottom:14px;">
      <div>
        <div style="font-size:42px;font-weight:800;color:{color};">{pct_text(avg)}%</div>
        <div style="font-size:12px;color:#6c757d;">Średnia maksymalna głębokość przewijania</div>
      </div>
      <div style="flex:1;min-width:180px;">
        <div style="background:#e9ecef;border-radius:6px;height:14px;overflow:hidden;">
          <div style="width:{pct}%;background:{color};height:100%;border-radius:6px;"></div>
        </div>
        <div style="font-size:12px;color:#6c757d;margin-top:4px;">zakres {lo}–{hi}% &nbsp;·&nbsp; {sessions}</div>
      </div>
    </div>
    {warning}
    {method}"""
    return _section("Głębokość przewijania", body)


def unchecked_links_text(links: dict) -> str:
    n = int((links or {}).get("unchecked_links") or 0)
    return (f"Nie udało się sprawdzić {n} linków — serwer nie odpowiedział na czas "
            "lub odrzucił połączenie. Nie liczymy ich ani jako działających, ani "
            "jako uszkodzonych.") if n else ""


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
          ✗ {len(broken_urls)} {pl_plural(len(broken_urls), "uszkodzony link", "uszkodzone linki", "uszkodzonych linków")} {sessions_in(sessions_broken)}
        </p>
        <ul style="list-style:none;padding-left:0;">{items}{more}</ul>"""

    body = f"""
    <p style="font-size:13px;color:#6c757d;margin-bottom:12px;">Sprawdzono {total} linków na odwiedzonych stronach.</p>
    {status_html}"""
    if links.get("unchecked_links"):
        body += f'<p class="sb-rules">{_esc(unchecked_links_text(links))}</p>' 
    return _section("Uszkodzone linki", body)


FORM_FUNNEL_NOTE = ("Wysłanie liczy się tylko wtedy, gdy po wpisaniu danych kliknięto "
                    "przycisk, który rzeczywiście wysyła formularz — nie link ani przycisk "
                    "nawigacji. Audyt nigdy nie wpisuje danych osobowych, więc formularzy "
                    "kontaktowych, rezerwacji i zamówień z założenia nie wysyła.")


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
        _step("Wpisali coś w pole (także wyszukiwarkę)", engaged, "#17a2b8") +
        _step("Wysłali formularz", submitted, "#008573")
    )
    if avg_fields is not None:
        body += f'<p style="font-size:13px;color:#6c757d;margin-top:12px;">Średnio {avg_text(avg_fields)} pola na sesję.</p>'
    # "Dotarli do akcji submit 1/3" was a link click counted as a submission.
    # Say how submission is judged — and that personal-data forms are never
    # sent by design, or a 0 here reads as the site failing.
    body += f'<p class="sb-rules">{_esc(FORM_FUNNEL_NOTE)}</p>' 
    return _section("Lejek formularzy", body)


def stats_table_rows(stats: dict, narr: dict | None = None) -> list[tuple[str, str]]:
    """The "Statystyki zbiorcze" rows — one list for the HTML and the Word file."""
    n = stats.get("participant_count", 0)
    sc = (stats.get("heuristic") or {}).get("severity_counts") or {}
    fi_avg = (stats.get("first_impression") or {}).get("avg_score")
    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    vp = stats.get("viewport") or {}
    counts = _issue_counts(narr)
    scanned = {d.get("viewport") for d in (vp.get("devices") or [])
               if isinstance(d, dict) and d.get("pages_scanned")}

    def overflow(device: str, key: str) -> str:
        if "devices" in vp and device not in scanned:
            return "nie zmierzono"
        return f"{vp.get(key, 0)}/{n} sesji"

    return [
        ("Uczestnicy", str(n)),
        ("Łącznie problemów", str(counts["total"])),
        ("Problemy krytyczne", str(counts["critical"])),
        ("Problemy poważne", str(counts["serious"])),
        ("Problemy drobne", str(counts["minor"])),
        ("Do weryfikacji przez człowieka", str(int(sc.get("needs_verification") or 0))),
        ("Pierwsze wrażenie (śr.)", f"{avg_text(fi_avg)}/5" if fi_avg else "—"),
        ("Dostępność (śr.)", f"{avg_text(a11y_avg)}/5" if a11y_avg else "—"),
        ("Overflow mobile", overflow("mobile", "mobile_overflow_sessions")),
        ("Overflow tablet", overflow("tablet", "tablet_overflow_sessions")),
    ]


def _render_stats_table(stats: dict, narr: dict | None = None) -> str:
    n = stats.get("participant_count", 0)
    heur = stats.get("heuristic") or {}
    sc = heur.get("severity_counts") or {}
    fi_avg = (stats.get("first_impression") or {}).get("avg_score")
    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    vp = stats.get("viewport") or {}

    def row(label, value, color=None):
        style = f' style="color:{color};font-weight:700;"' if color else ""
        return f"<tr><td>{_esc(label)}</td><td{style}>{_esc(value)}</td></tr>"

    colors = {"Problemy krytyczne": "#E60000", "Problemy poważne": "#856404",
              "Problemy drobne": "#0c5460"}
    rows = "".join(row(label, value, colors.get(label))
                   for label, value in stats_table_rows(stats, narr))
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


_OUTCOME_PL = {
    "success": ("Cel osiągnięty", "#1f7a4d", "#e9f6ee"),
    "partial": ("Cel osiągnięty częściowo", "#8c5a12", "#fbf2e3"),
    "failed": ("Cel nieosiągnięty", "#b3352f", "#fdeceb"),
    # No verdict: the reflection call failed or returned something else.
    "unknown": ("Wynik nieustalony", "#5b6470", "#f1f2f4"),
    "persona_abandoned": ("Sesja przerwana", "#5b6470", "#f1f2f4"),
}

_SOURCE_PL = {
    "observed": "zmierzone z zapisu sesji",
    "session": "przerwane przez narzędzie po kilku identycznych próbach bez zmiany strony",
    "declared": "werdykt modelu, niezmierzony",
}


def objective_outcome(r: dict) -> str:
    """The outcome the score counts for this participant, else the model's verdict."""
    return r.get("effective_outcome") or r.get("outcome") or "unknown"


SELF_REPORTED_CAVEAT = ("⚠ Relacja uczestnika, niezweryfikowana względem zapisu sesji — "
                        "część opisanych trudności może wynikać z ograniczeń narzędzia, "
                        "a nie z serwisu.")
OBJECTIVES_NOTE = ("Poniższe relacje pochodzą od uczestników badania i opisują ich "
                   "własne odczucia z sesji. W odróżnieniu od sekcji „Problemy\" nie są "
                   "automatycznie weryfikowane względem zapisu sesji — część opisanych "
                   "trudności może wynikać z ograniczeń narzędzia, a nie z serwisu.")


def held_claims_note(stats: dict, persona: str | None) -> str:
    """What the gate held back from this participant, for their reflection card.

    A reflection is written from the same session as the findings, so it can
    repeat a claim the verifier set aside — one persona's "Mandatory Field"
    blocker reached the client that way although the finding itself was held.
    Matching the prose to the finding would be guesswork, so nothing is matched:
    the card simply says what was held for this person and that any similar
    remark in their summary is unconfirmed. "" when nothing was held.
    """
    items = [it for it in ((stats.get("heuristic") or {}).get("needs_verification_items") or [])
             if persona and it.get("persona_name") == persona]
    if not items:
        return ""
    parts = []
    for it in items[:3]:
        claim = str(it.get("evidence") or "").strip()
        if len(claim) > 110:
            claim = claim[:110].rsplit(" ", 1)[0] + "…"
        parts.append(f"krok {it.get('step')}: „{claim}”")
    return ("Wnioski tej osoby wstrzymane po weryfikacji (szczegóły w sekcji „Do weryfikacji "
            f"przez człowieka”): {'; '.join(parts)}. Jeśli podobne uwagi pojawiają się w jej "
            "podsumowaniu poniżej, traktuj je jako niepotwierdzone.")


def _render_objectives(stats: dict) -> str:
    """What each participant was asked to do, and how far they got.

    There was no such section: the objective reached the page once, as the
    header subtitle, so an audit of a car configurator and an audit of a
    checkout produced the same document — the reviewer asked for a report on a
    configurator's strengths and weaknesses and got the standard one back.

    It is also the only place the reader learns whether the task was actually
    completed, which is the headline result of any usability test. That was
    computed for every persona and rendered nowhere.
    """
    rows = stats.get("objective_reflections") or []
    if not rows:
        return ""

    counts: dict[str, int] = {}
    for r in rows:
        key = objective_outcome(r)
        counts[key] = counts.get(key, 0) + 1
    tally = " · ".join(
        f'<span style="color:{_OUTCOME_PL.get(k, ("", "#444", ""))[1]};font-weight:700">'
        f'{_OUTCOME_PL.get(k, (k, "", ""))[0]}: {v}</span>'
        for k, v in sorted(counts.items())
    )

    cards = ""
    for r in rows:
        label, fg, bg = _OUTCOME_PL.get(objective_outcome(r), ("—", "#444", "#f1f2f4"))
        # Where the badge comes from, and what the model said when it differs.
        src = _SOURCE_PL.get(r.get("outcome_source") or "")
        declared = r.get("outcome") or ""
        if src and r.get("outcome_source") != "declared" and declared and declared != objective_outcome(r):
            src += f"; model ocenił: {_OUTCOME_PL.get(declared, (declared,))[0].lower()}"
        source_html = f'<div class="obj-src">{_esc(src)}</div>' if src else ""
        blockers = "".join(f"<li>{_esc(b)}</li>" for b in (r.get("blockers") or [])[:4])
        wins = "".join(f"<li>{_esc(w)}</li>" for w in (r.get("wins") or [])[:4])
        fix = _esc(r.get("priority_fix") or "")
        # A reflection is the persona's own account, never checked against the
        # trace. When it names our machinery or repeats a claim the gate threw
        # out, rendering it as plain fact is how a suppressed finding still
        # reaches the client — which is exactly what both re-run reports did.
        unverified = (f'<div class="obj-caveat">{_esc(SELF_REPORTED_CAVEAT)}</div>'
                      if r.get("self_reported_only") else "")
        held = held_claims_note(stats, r.get("persona"))
        if held and not r.get("self_reported_only"):
            unverified += f'<div class="obj-caveat">{_esc(held)}</div>'
        cards += f"""
        <div class="obj-card">
          <div class="obj-head">
            <span class="obj-who">{_esc(r.get("persona") or "—")}</span>
            <span class="obj-outcome" style="background:{bg};color:{fg}">{label}</span>
          </div>
          {source_html}
          {unverified}
          <div class="obj-task">{_esc(r.get("objective") or "")}</div>
          <p class="obj-summary">{_esc(r.get("summary") or "")}</p>
          <div class="obj-cols">
            <div><div class="obj-col-h" style="color:#b3352f">Co przeszkadzało</div>
              <ul class="obj-list">{blockers or "<li>—</li>"}</ul></div>
            <div><div class="obj-col-h" style="color:#1f7a4d">Co zadziałało dobrze</div>
              <ul class="obj-list">{wins or "<li>—</li>"}</ul></div>
          </div>
          {f'<div class="obj-fix"><span class="obj-fix-l">Najważniejsza poprawka</span>{fix}</div>' if fix else ""}
        </div>"""

    # Standing caveat on the whole section, not just the reflections a rule
    # managed to flag. Every reflection here is the participant's own account,
    # written from the same session and never checked against the trace — so a
    # blocker can restate a finding the verification gate threw out, and in the
    # hyundai run all four of one persona's blockers did exactly that. Detecting
    # which ones proved unreliable (see attribution.restates_a_gated_finding),
    # but saying plainly what this section IS costs nothing and is always true.
    note = f'<p class="obj-note">{_esc(OBJECTIVES_NOTE)}</p>' 
    return _section("Realizacja zadania",
                     f'{note}<div class="obj-tally">{tally}</div>{cards}')


_DEDUCTION_KIND_PL = {
    "issue": "Problem użyteczności",
    "task": "Realizacja zadania",
    "accessibility": "Dostępność",
    "performance": "Wydajność",
    "adjustment": "Korekta",
}


def _render_score_breakdown(stats: dict) -> str:
    """How the score was computed, line by line, so it can be checked.

    The score used to be the model's own judgement with a prose rationale, so
    there was nothing to add up. It is arithmetic now (scoring.py): the reader
    sees the starting point, every deduction with the rule that sized it, the
    total, and what was deliberately left out. Anyone can disagree with a weight
    here; nobody has to take the number on trust.
    """
    b = stats.get("score_breakdown") or {}
    if not b or b.get("score") is None:
        return ""
    rows = f"""
        <tr class="sb-start"><td>Punkt wyjścia</td><td></td>
            <td class="sb-amt">{decimal_text(b['start'], 1)}</td></tr>"""
    for d in b.get("deductions") or []:
        amt = d.get("amount", 0)
        sign = "−" if amt >= 0 else "+"
        rows += f"""
        <tr><td><span class="sb-kind">{_esc(_DEDUCTION_KIND_PL.get(d.get('kind'), ''))}</span>
                {_esc(d.get('label', ''))}</td>
            <td class="sb-basis">{_esc(d.get('basis', ''))}</td>
            <td class="sb-amt">{sign}{decimal_text(abs(amt), 2)}</td></tr>"""
    floored = " (ograniczone do minimum 1,0)" if b.get("floored") else ""
    rows += f"""
        <tr class="sb-sum"><td>Suma odliczeń</td><td></td>
            <td class="sb-amt">−{decimal_text(b['total_deduction'], 2)}</td></tr>
        <tr class="sb-final"><td>Ocena końcowa</td>
            <td class="sb-basis">{decimal_text(b['start'], 1)} − {decimal_text(b['total_deduction'], 2)} =
                {decimal_text(b['raw'], 2)}, zaokrąglone{floored}</td>
            <td class="sb-amt">{decimal_text(b['score'], 1)}</td></tr>"""

    left_out = "".join(
        f"<li><b>{_esc(n.get('label', ''))}</b> — {_esc(n.get('reason', ''))}</li>"
        for n in b.get("not_counted") or []
    )
    rules = b.get("rules") or {}
    issue = rules.get("issue") or {}
    rule_note = (
        "Reguły: problem krytyczny {k}, poważny {p}, drobny {d} punktu, "
        "pomnożone przez zasięg i pewność dowodu. Zasięg = 0,5 + 0,5 × odsetek "
        "uczestników, którzy napotkali problem — rzeczywista wada zauważona przez "
        "jedną osobę nie jest trzecią częścią wady. Nieukończone zadanie {tf}, "
        "częściowo {tp} na uczestnika — o połowę mniej, gdy to ocena deklarowana, a "
        "nie zmierzona. Wagi są jawne i można je zmienić."
    ).format(k=issue.get("krytyczny"), p=issue.get("poważny"), d=issue.get("drobny"),
             tf=(rules.get("task") or {}).get("failed"),
             tp=(rules.get("task") or {}).get("partial"))
    body = f"""
      <table class="sb-table">{rows}</table>
      {f'<div class="sb-left-h">Nie wliczono do oceny</div><ul class="sb-left">{left_out}</ul>' if left_out else ''}
      <p class="sb-rules">{_esc(rule_note)}</p>"""
    return _section("Jak obliczono ocenę", body)


def _unconfirmed_note(narr: dict | None) -> str:
    """The narrator's issues that no confirmed finding supports, named, not counted."""
    items = [i for i in ((narr or {}).get("unconfirmed_issues") or []) if isinstance(i, dict)]
    if not items:
        return ""
    names = "; ".join((f"H{i['heuristic_id']}: " if isinstance(i.get("heuristic_id"), int) else "")
                      + str(i.get("heuristic_name") or "") for i in items)
    if len(items) == 1:
        return (f"Pominięto też 1 problem z podsumowania, dla którego żadne ustalenie nie "
                f"zostało potwierdzone — {names}. Jego źródłowe wnioski są na liście "
                "poniżej albo nie przeszły weryfikacji.")
    many = pl_plural(len(items), "problem", "problemy", "problemów")
    return (f"Pominięto też {len(items)} {many} z podsumowania, dla których żadne "
            f"ustalenie nie zostało potwierdzone — {names}. Ich źródłowe wnioski są "
            "na liście poniżej albo nie przeszły weryfikacji.")


def _render_verification_list(stats: dict, narr: dict | None = None) -> str:
    """Findings the verification gate held back, each with what would settle it.

    They used to be a count. A specialist cannot act on a count — she can act on
    "click EXECUTIVE MY27 in a normal browser and see whether it selects", and
    if it does not, she has found a real defect the automation could only
    suspect. That is also why they are shown rather than dropped: excluded is
    not the same as false.
    """
    items = (stats.get("heuristic") or {}).get("needs_verification_items") or []
    note = _unconfirmed_note(narr)
    obstacles = [o for o in (stats.get("repeated_obstacles") or []) if isinstance(o, dict)]
    recurring = [t for t in (stats.get("recurring_reports") or []) if isinstance(t, dict)]
    if not items and not note and not obstacles and not recurring:
        return ""
    cards = ""
    for it in items:
        shot = it.get("screenshot")
        thumb = (f'<a href="{_esc(shot)}" target="_blank"><img class="ev-thumb" '
                 f'src="{_esc(shot)}" alt="krok {it.get("step")}"/></a>') if shot else ""
        h_id = it.get("heuristic_id")
        title = (f"H{h_id}: {it.get('heuristic_name', '')}" if isinstance(h_id, int) and h_id
                 else it.get("heuristic_name", "") or "Wniosek")
        claimed = it.get("severity_claimed") or ""
        seen = str(it.get("verifier_reason") or "").strip()
        verifier_html = (f'<div class="nv-why"><b>Co pokazuje zapis sesji:</b> {_esc(seen)}</div>'
                         if seen else "")
        cards += f"""
        <div class="nv-card">
          <div class="nv-head"><b>{_esc(title)}</b>
            <span class="nv-meta">zgłoszone jako {_esc(claimed)} · {_esc(it.get('persona_name', ''))}, krok {it.get('step')}</span></div>
          <div class="nv-claim">„{_esc(clip_words(it.get('evidence', ''), 300))}”</div>
          <div class="nv-why"><b>Dlaczego wstrzymane:</b> {_esc(it.get('reason', ''))}</div>
          {verifier_html}
          <div class="nv-check"><b>Co sprawdzić:</b> {_esc(it.get('check', ''))}</div>
          {thumb}
        </div>"""
    intro = held_intro_text(len(items))
    if not items:
        intro = ""
    note_html = f'<p class="sb-rules">{_esc(note)}</p>' if note else ""
    intro_html = f'<p class="sb-rules">{_esc(intro)}</p>' if intro else ""
    extra = ""
    if recurring:
        rows = "".join(f"<li>{_esc(theme_text(t))}</li>" for t in recurring)
        extra += ('<div class="nv-sub" style="font-weight:700;margin-top:16px;">Opisywane przez wielu '
                  f'uczestników</div><p class="sb-rules">{_esc(RECURRING_INTRO)}</p><ul>{rows}</ul>')
    if obstacles:
        rows = "".join(f"<li>{_esc(obstacle_text(o))}</li>" for o in obstacles)
        extra += ('<div class="nv-sub" style="font-weight:700;margin-top:16px;">Powtarzające się '
                  f'przeszkody w klikaniu</div><p class="sb-rules">{_esc(OBSTACLES_INTRO)}</p><ul>{rows}</ul>')
    return _section("Do weryfikacji przez człowieka", f'{intro_html}{note_html}{cards}{extra}')


def _render_heuristic_legend(stats: dict) -> str:
    """All ten heuristics with their verdict, plus what the method is.

    Issues render as "H1", "H3" with no key anywhere, so a reader cannot tell
    what was assessed, how many of the ten passed, or what the numbers mean.
    Heuristics with no finding are shown as "no violation observed" — the
    honest wording, since the auditor is told to omit passing heuristics rather
    than to certify them.
    """
    from .reporter import HEURISTIC_NAMES

    by_h = {
        h.get("heuristic_id"): h
        for h in ((stats.get("heuristic") or {}).get("by_heuristic") or [])
    }
    held_back = {int(k): v for k, v in
                 ((stats.get("heuristic") or {}).get("excluded_by_heuristic") or {}).items()
                 if str(k).lstrip("-").isdigit()}
    # "No violation observed" needs something to have been observed: a run
    # whose sessions never loaded printed ten green passes.
    evaluated = _anything_observed(stats)
    rows = ""
    for h_id in sorted(HEURISTIC_NAMES):
        hit = by_h.get(h_id)
        pending = held_back.get(h_id, 0)
        pending_note = (f' <span style="color:#8a6d00">· {pending} do weryfikacji</span>'
                        if pending else "")
        if hit:
            sev = hit.get("worst_severity", "drobny")
            _bg, fg, _bd = SEVERITY_COLORS.get(sev, ("#f8f9fa", "#212529", "#dee2e6"))
            verdict = (f'<span style="color:{fg};font-weight:700">{_esc(sev)}</span> '
                       f'<span style="color:#6c757d">· {_esc(hit.get("sessions_affected", ""))}</span>'
                       f'{pending_note}')
        elif pending:
            # Not "no violation": something was raised here and set aside. The
            # list below says what, and what would settle it.
            verdict = ('<span style="color:#6c757d">brak potwierdzonych naruszeń</span>'
                       f'{pending_note}')
        elif not evaluated:
            verdict = '<span style="color:#6c757d">nie oceniono</span>'
        else:
            verdict = '<span style="color:#1f7a4d">nie stwierdzono naruszeń</span>'
        rows += (f'<tr><td class="hl-id">H{h_id}</td>'
                 f'<td class="hl-name">{_esc(HEURISTIC_NAMES[h_id])}</td>'
                 f'<td class="hl-verdict">{verdict}</td></tr>')

    intro = (
        "Ocena opiera się na dziesięciu heurystykach użyteczności Nielsena — "
        "uznanym zestawie reguł, według których ocenia się interfejsy. Każdy "
        "problem użyteczności w raporcie jest przypisany do jednej z nich i "
        "oznaczony jako H1–H10; bariery dostępności i wydajności są opisane "
        "osobno. Poniżej pełna lista wraz z wynikiem dla badanego serwisu."
    )
    return _section(
        "Heurystyki Nielsena — zakres oceny",
        f'<p class="hl-intro">{intro}</p>'
        f'<table class="hl-table">{rows}</table>',
    )


def _render_score_reasons(stats: dict) -> str:
    """Why the first-impression dimensions scored what they did.

    The reviewer's sharpest complaint was that the report gives scores without
    ever saying what lowered them. The answer existed per persona all along —
    first_impression.txt asks for a rationale per dimension, grounded in the
    step-0 screenshot — and aggregation kept only the numbers.
    """
    fi = stats.get("first_impression") or {}
    reasons = fi.get("dimension_reasons") or {}
    weaknesses = fi.get("weaknesses") or []
    if not reasons and not weaknesses:
        return ""

    blocks = ""
    for key, label in _FI_DIM_LABELS.items():
        items = reasons.get(key) or []
        if not items:
            continue
        lis = "".join(
            f'<li><span class="sr-who">{_esc(i.get("persona") or "—")}'
            f'{f" · {i['score']}/5" if i.get("score") is not None else ""}</span>'
            f'{_esc(i.get("why") or "")}'
            # The reviewer's next question after "why": what to change.
            + (f'<div class="sr-fix"><b>Propozycja zmiany:</b> {_esc(i["change"])}</div>'
               if i.get("change") else "")
            + '</li>'
            for i in items
        )
        blocks += f'<div class="sr-dim"><div class="sr-dim-h">{_esc(label)}</div><ul>{lis}</ul></div>'
    if any(i.get("change") for items in reasons.values() for i in items or []):
        blocks = ('<p class="sv-legend">Przy każdym powodzie — zmiana, która usunęłaby '
                  'tę barierę dla tej osoby, zaproponowana w tej samej ocenie '
                  'pierwszego ekranu. To propozycja do rozważenia, nie zmierzony '
                  'wynik.</p>' + blocks)

    if weaknesses:
        lis = "".join(
            f'<li><span class="sr-who">{_esc(w.get("persona") or "—")}</span>'
            f'{_esc(w.get("text") or "")}</li>' for w in weaknesses[:10]
        )
        blocks += f'<div class="sr-dim"><div class="sr-dim-h">Co obniżyło pierwsze wrażenie</div><ul>{lis}</ul></div>'

    return _section("Dlaczego takie oceny", blocks) if blocks else ""


def _render_accessibility_detail(stats: dict) -> str:
    """The actual accessibility barriers, not just the average score.

    The section rendered a single number; the pass produces categorised
    findings with the page they were seen on and a recommendation.
    """
    from .scoring import accessibility_severity

    a11y = stats.get("accessibility") or {}
    findings = a11y.get("findings") or []
    if not findings:
        return ""
    images = (a11y.get("measured") or {}).get("images") or {}
    rows = ""
    for f in findings[:12]:
        # The same rule the issue cards and the score apply. The review found
        # alt text "poważny" here and "drobny" in the issue list — one finding,
        # two severities, depending on where the reader looked.
        sev, measured_text = accessibility_severity(f, images)
        measured = f'<div class="measured">{_esc(measured_text)}</div>' if measured_text else ""
        # Other barriers of the same kind, on other pages, with their own text
        # — merged into this entry, never presented as its proof.
        measured += "".join(
            f'<div class="a11y-also"><b>Również:</b> {_esc(o.get("detail") or "")} '
            f'<span class="a11y-where">{_esc(display_url(o.get("url") or ""))}</span></div>'
            for o in (f.get("other_instances") or [])[:3])
        bg, fg, border = SEVERITY_COLORS.get(sev, ("#f8f9fa", "#212529", "#dee2e6"))
        where = clip_words(", ".join(dict.fromkeys(display_url(u) for u in (f.get("urls") or []))), 160)
        who = ", ".join(f.get("personas") or [])
        rows += f"""
        <div class="a11y-item" style="border-left-color:{border}">
          <div class="a11y-head">
            <span class="a11y-sev" style="background:{bg};color:{fg}">{_esc(sev)}</span>
            <span class="a11y-cat">{_esc(f.get("category") or "")}</span>
            {f'<span class="a11y-who">{_esc(who)}</span>' if who else ""}
          </div>
          <div class="a11y-detail">{_esc(f.get("detail") or "")}</div>
          {measured}
          {f'<div class="a11y-fix"><span class="a11y-fix-l">Rekomendacja</span>{_esc(f.get("recommendation"))}</div>' if f.get("recommendation") else ""}
          {f'<div class="a11y-where">{_esc(where)}</div>' if where else ""}
        </div>"""
    return _section("Bariery dostępności", rows)


_DEVICE_LABELS_PL = {"desktop": "na desktopie", "mobile": "na telefonie"}


def _session_device_phrase(stats: dict) -> str:
    """How to describe the width the walkthrough actually used.

    The section used to assert "w oknie desktopowym" unconditionally. That was
    true while every run was desktop; once the run can pick, an unchecked
    sentence in a client-facing report becomes a false statement about method.
    Runs recorded before the field existed say nothing rather than guess.
    """
    counts = (stats.get("viewport") or {}).get("session_devices") or {}
    known = {k: v for k, v in counts.items() if k in _DEVICE_LABELS_PL and v}
    if not known:
        return "w oknie przeglądarki o stałej szerokości"
    if len(known) == 1:
        return _DEVICE_LABELS_PL[next(iter(known))]
    parts = [f"{_DEVICE_LABELS_PL[k]} ({v})" for k, v in sorted(known.items())]
    return "częściowo " + ", częściowo ".join(parts)


def _render_device_detail(stats: dict) -> str:
    """Per-device measurements the scan already takes and nothing read.

    Answers "how does it behave on mobile?" with numbers rather than prose.
    The caveat matters and is stated: this is a technical scan at each
    viewport, not a journey — the journeys ran at whatever width the run
    selected, which _session_device_phrase reads back from the sessions.
    """
    devices = (stats.get("viewport") or {}).get("devices") or []
    if not devices:
        return ""
    cards = ""
    for d in devices:
        small = ", ".join(
            str(s.get("size")) for s in (d.get("small_font_examples") or [])[:3]
            if isinstance(s, dict) and s.get("size")
        )
        cards += f"""
        <div class="dev-card">
          <div class="dev-name">{_esc(d.get("viewport") or "")}</div>
          <table class="dev-table">
            <tr><td>Przeskanowane strony</td><td class="dev-v">{d.get("pages_scanned", 0)}</td></tr>
            <tr><td>Strony z poziomym przewijaniem</td><td class="dev-v">{d.get("pages_with_overflow", 0)}</td></tr>
            <tr><td>Elementy dotykowe poniżej minimum (WCAG 2.5.8)</td><td class="dev-v">{d.get("tap_targets_below_min", 0)}</td></tr>
            <tr><td>Teksty poniżej kontrastu AA</td><td class="dev-v">{d.get("contrast_failures", 0)}</td></tr>
            {f'<tr><td>Najmniejsze wykryte czcionki</td><td class="dev-v">{_esc(small)}</td></tr>' if small else ""}
          </table>
        </div>"""
    caveat = (
        "Pomiar techniczny wykonany osobno dla każdej szerokości ekranu. "
        "Te wyniki opisują układ strony, a nie przebieg ścieżki użytkownika — "
        "problemy heurystyczne pochodzą z sesji agentów, które odbyły się "
        f"{_session_device_phrase(stats)}."
    )
    return _section("Zachowanie na urządzeniach",
                     f'<div class="dev-grid">{cards}</div>'
                     f'<p class="dev-caveat">{caveat}</p>')


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def generate_html_report(agg: dict) -> str:
    stats = agg.get("statistics") or {}
    ps = agg.get("pagespeed")

    from .reporter import resolve_narrative  # avoid circular at module level
    narr = resolve_narrative(agg)

    scroll = agg.get("scroll")
    links = agg.get("links")
    forms = agg.get("forms")

    body = (
        _render_header(agg, stats, narr) +
        '<div class="page">' +
        _render_executive(narr) +
        # Karolina: list every heuristic at the start, so the reader knows how
        # many are fine before reading any issue. It also has to come before the
        # score breakdown, whose lines cite "H1", "H9" by number.
        _render_heuristic_legend(stats) +
        _render_score_breakdown(stats) +
        # What was asked and whether it was achieved, before any heuristic
        # detail: a usability report that opens with abstract scores buries
        # its own headline.
        _render_objectives(stats) +
        _render_strengths(narr, stats) +
        # The key to "H1", "H3" — without it the issues below name a system the
        # reader has never been shown.
        _render_all_issues(narr) +
        _render_verification_list(stats, narr) +
        _render_recommendations(narr) +
        _render_pagespeed(ps) +
        _render_scroll_depth(scroll) +
        _render_broken_links(links) +
        _render_form_funnel(forms) +
        _render_first_impression(stats, narr) +
        # Immediately after the scores, so a low number and its explanation are
        # read together rather than pages apart.
        _render_score_reasons(stats) +
        _render_survey(stats, agg.get("survey_summaries")) +
        _render_accessibility(stats, narr) +
        _render_accessibility_detail(stats) +
        _render_responsiveness(stats, narr) +
        _render_device_detail(stats) +
        _render_stats_table(stats, narr) +
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


def refresh_html_report(run_dir: Path) -> Path | None:
    """The run's HTML report as the current code renders it.

    The file used to be written once, at aggregation, and served as it was: a
    fix to the renderer never reached a run made before it, while the Word file
    was built by current code on first download — so one run shipped two sets
    of numbers (4 of 9 archived reports had different header counts). Rendering
    takes ~10 ms, so it is redone from the JSON on every request. The file is
    rewritten only when its content changed, which is what the PDF cache keys
    on, so the PDF is re-rendered exactly when the report is different.
    None when there is no report; the stored file alone when the JSON is gone.
    """
    import json

    run_dir = Path(run_dir)
    out = run_dir / "aggregated_report.html"
    src = run_dir / "aggregated_report.json"
    try:
        agg = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out if out.is_file() else None
    html_str = generate_html_report(agg)
    try:
        current = out.read_text(encoding="utf-8") if out.is_file() else None
    except OSError:
        current = None
    if current != html_str:
        out.write_text(html_str, encoding="utf-8")
    return out
