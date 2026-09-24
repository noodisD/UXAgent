"""
The report as an editable Word document.

Asked for by the first external reviewer: the HTML and the PDF are read-only,
so a UX specialist who wants to adapt the wording, cut a section or drop the
report into a client deck has nowhere to start from.

Built from aggregated_report.json rather than by converting the HTML. A
converted document arrives as one blob of styled text: the headings are not
Word headings, the tables are not Word tables, and editing it is worse than
starting over. Going from the data means every heading is a real heading (so
the navigation pane and a generated contents page work), every table is a real
table, and the screenshots are embedded pictures a person can move or delete.

Section order deliberately matches reporter_html so the three formats are
recognisably the same document.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Havas red, as used by the HTML report.
_ACCENT = (0xE6, 0x00, 0x00)
_MUTED = (0x6B, 0x6B, 0x6B)

_SEVERITY_LABEL = {
    "krytyczny": "KRYTYCZNY",
    "poważny": "POWAŻNY",
    "drobny": "DROBNY",
}
_OUTCOME_LABEL = {
    "success": "Cel osiągnięty",
    "partial": "Cel osiągnięty częściowo",
    "failed": "Cel nieosiągnięty",
}

_FI_DIM_LABELS = {
    "purpose_clarity": "Jasność celu",
    "navigation_visibility": "Widoczność nawigacji",
    "call_to_action": "Wezwanie do działania",
    "visual_hierarchy": "Hierarchia wizualna",
    "trustworthiness": "Wiarygodność",
}


def _add_caption(doc, text: str) -> None:
    from docx.shared import Pt, RGBColor

    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(8.5)
    run.font.color.rgb = RGBColor(*_MUTED)


def _add_kv_table(doc, rows: list[tuple[str, str]]) -> None:
    if not rows:
        return
    table = doc.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"
    for key, value in rows:
        cells = table.add_row().cells
        cells[0].text = str(key)
        cells[1].text = str(value)


def _add_picture(doc, run_dir: Path, rel_path: str, caption: str = "") -> bool:
    """Embed a run-relative screenshot. Returns whether it was added.

    Silently skips a missing file: a report that renders without one image is
    better than one that fails to build at all.
    """
    from docx.shared import Inches

    if not rel_path:
        return False
    src = (Path(run_dir) / str(rel_path)).resolve()
    if not src.is_file():
        return False
    try:
        doc.add_picture(str(src), width=Inches(5.6))
    except Exception:
        log.exception("[docx] could not embed %s", src)
        return False
    if caption:
        _add_caption(doc, caption)
    return True


def _issues(narr: dict) -> list[tuple[str, list]]:
    return [
        ("Problemy krytyczne", narr.get("critical_issues") or []),
        ("Problemy poważne", narr.get("serious_issues") or []),
        ("Problemy drobne", narr.get("minor_issues") or []),
    ]


def build_docx(agg: dict, run_dir: Path, out_path: Path) -> Path:
    """Write the aggregated report to `out_path` as .docx and return the path."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    from .reporter import HEURISTIC_NAMES, resolve_narrative

    stats = agg.get("statistics") or {}
    narr = resolve_narrative(agg)
    doc = Document()

    # ── Title ────────────────────────────────────────────────────────────
    title = doc.add_heading("Raport UX", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    sub = doc.add_paragraph()
    run = sub.add_run(str(agg.get("website") or ""))
    run.bold = True
    run.font.color.rgb = RGBColor(*_ACCENT)
    if agg.get("task"):
        _add_caption(doc, f"Zadanie: {agg['task']}")

    score = narr.get("overall_ux_score")
    _add_kv_table(doc, [
        ("Uczestnicy", stats.get("participant_count", "—")),
        ("Ocena ogólna", f"{score}/5" if score is not None else "—"),
        ("Problemy krytyczne", (stats.get("heuristic") or {}).get(
            "severity_counts", {}).get("krytyczny", 0)),
        ("Problemy poważne", (stats.get("heuristic") or {}).get(
            "severity_counts", {}).get("poważny", 0)),
    ])

    # ── Summary ──────────────────────────────────────────────────────────
    if narr.get("executive_summary"):
        doc.add_heading("Podsumowanie", level=1)
        doc.add_paragraph(str(narr["executive_summary"]))
        if narr.get("score_rationale"):
            _add_caption(doc, str(narr["score_rationale"]))

    # ── Task outcomes ────────────────────────────────────────────────────
    reflections = stats.get("objective_reflections") or []
    if reflections:
        doc.add_heading("Realizacja zadania", level=1)
        for r in reflections:
            doc.add_heading(
                f"{r.get('persona', '—')} — "
                f"{_OUTCOME_LABEL.get(r.get('outcome'), r.get('outcome') or '—')}",
                level=2,
            )
            if r.get("objective"):
                _add_caption(doc, f"Cel: {r['objective']}")
            if r.get("summary"):
                doc.add_paragraph(str(r["summary"]))
            for heading, key in (("Co przeszkadzało", "blockers"),
                                 ("Co zadziałało dobrze", "wins")):
                items = [x for x in (r.get(key) or []) if str(x).strip()]
                if not items:
                    continue
                p = doc.add_paragraph()
                p.add_run(heading).bold = True
                for item in items:
                    doc.add_paragraph(str(item), style="List Bullet")
            if r.get("priority_fix"):
                p = doc.add_paragraph()
                p.add_run("Najważniejsza poprawka: ").bold = True
                p.add_run(str(r["priority_fix"]))

    # ── Strengths ────────────────────────────────────────────────────────
    strengths = [s for s in (narr.get("key_strengths") or []) if str(s).strip()]
    if strengths:
        doc.add_heading("Mocne strony", level=1)
        for s in strengths:
            doc.add_paragraph(str(s), style="List Bullet")

    # ── Heuristic legend ─────────────────────────────────────────────────
    by_h = {h.get("heuristic_id"): h
            for h in ((stats.get("heuristic") or {}).get("by_heuristic") or [])}
    doc.add_heading("Heurystyki Nielsena — zakres oceny", level=1)
    doc.add_paragraph(
        "Ocena opiera się na dziesięciu heurystykach użyteczności Nielsena. "
        "Każdy problem w raporcie jest przypisany do jednej z nich (H1–H10)."
    )
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "", "Heurystyka", "Wynik"
    for h_id in sorted(HEURISTIC_NAMES):
        hit = by_h.get(h_id)
        cells = table.add_row().cells
        cells[0].text = f"H{h_id}"
        cells[1].text = HEURISTIC_NAMES[h_id]
        cells[2].text = (
            f"{hit.get('worst_severity', '')} · {hit.get('sessions_affected', '')}"
            if hit else "nie stwierdzono naruszeń"
        )

    # ── Issues, with their evidence ──────────────────────────────────────
    for heading, issues in _issues(narr):
        if not issues:
            continue
        doc.add_heading(heading, level=1)
        for issue in issues:
            sev = issue.get("severity") or ""
            h_id = issue.get("heuristic_id")
            # WCAG issues have no Nielsen heuristic; "HNone:" was the title.
            prefix = f"H{h_id}: " if isinstance(h_id, int) else ""
            doc.add_heading(f"{prefix}{issue.get('heuristic_name', '')}", level=2)
            meta = []
            if _SEVERITY_LABEL.get(sev):
                meta.append(_SEVERITY_LABEL[sev])
            if issue.get("frequency"):
                meta.append(str(issue["frequency"]))
            if issue.get("verified") is False:
                meta.append("NIEZWERYFIKOWANE")
            if meta:
                _add_caption(doc, " · ".join(meta))
            if issue.get("impact"):
                doc.add_paragraph(str(issue["impact"]))
            # Same measured denominator and demotion note as the HTML: this is
            # the copy a client edits and forwards, so it must not be the one
            # place the number is missing.
            measured = issue.get("measured") or {}
            if measured.get("images_total"):
                pct = round(100 * (measured.get("share_unnamed") or 0))
                p = doc.add_paragraph()
                p.add_run(f"Zmierzono: {measured['images_unnamed']} z "
                          f"{measured['images_total']} obrazów bez tekstu "
                          f"alternatywnego ({pct}%).").bold = True
            if issue.get("demotion_reason"):
                was = {"critical_issues": "krytyczne", "serious_issues": "poważne"}.get(
                    issue.get("demoted_from"), "wyższej kategorii")
                why = ("pomiar pokazuje, że dotyczy to niewielkiej części elementów"
                       if issue["demotion_reason"] == "measured_share"
                       else "brak dowodu w zapisie sesji")
                _add_caption(doc, f"Przeniesiono z kategorii „{was}”: {why}.")
            if issue.get("recommendation"):
                p = doc.add_paragraph()
                p.add_run("Rekomendacja: ").bold = True
                p.add_run(str(issue["recommendation"]))
            for ex in (issue.get("examples") or [])[:2]:
                caption = " · ".join(x for x in [
                    ex.get("persona_name"),
                    f"krok {ex['step']}" if ex.get("step") is not None else "",
                    ex.get("url"),
                ] if x)
                _add_picture(doc, run_dir, ex.get("screenshot"), caption)

    # ── Recommendations ──────────────────────────────────────────────────
    recs = narr.get("priority_recommendations") or []
    if recs:
        doc.add_heading("Priorytetowe rekomendacje", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text = "#", "Obszar", "Działanie"
        for r in recs:
            cells = table.add_row().cells
            cells[0].text = str(r.get("priority", ""))
            cells[1].text = str(r.get("area", ""))
            action = str(r.get("action", ""))
            if r.get("expected_impact"):
                action += f"\nOczekiwany efekt: {r['expected_impact']}"
            cells[2].text = action

    # ── First impression ─────────────────────────────────────────────────
    fi = stats.get("first_impression") or {}
    if fi.get("avg_score") is not None or fi.get("dimension_avgs"):
        doc.add_heading("Pierwsze wrażenie (test 5 sekund)", level=1)
        if fi.get("avg_score") is not None:
            doc.add_paragraph(f"Średnia ocena: {fi['avg_score']}/5")
        for s in (fi.get("screenshots") or [])[:3]:
            caption = str(s.get("persona") or "")
            if s.get("score") is not None:
                caption = f"{caption} · {s['score']}/5".lstrip(" ·")
            _add_picture(doc, run_dir, s.get("screenshot"), caption)
        dims = [(label, (fi.get("dimension_avgs") or {}).get(key))
                for key, label in _FI_DIM_LABELS.items()
                if (fi.get("dimension_avgs") or {}).get(key) is not None]
        _add_kv_table(doc, [(l, f"{v}/5") for l, v in dims])

        reasons = fi.get("dimension_reasons") or {}
        if reasons:
            doc.add_heading("Dlaczego takie oceny", level=2)
            for key, label in _FI_DIM_LABELS.items():
                items = reasons.get(key) or []
                if not items:
                    continue
                p = doc.add_paragraph()
                p.add_run(label).bold = True
                for i in items:
                    who = i.get("persona", "—")
                    sc = f" ({i['score']}/5)" if i.get("score") is not None else ""
                    doc.add_paragraph(f"{who}{sc}: {i.get('why', '')}",
                                      style="List Bullet")

    # ── Accessibility ────────────────────────────────────────────────────
    acc = stats.get("accessibility") or {}
    if acc.get("avg_score") is not None or acc.get("findings"):
        doc.add_heading("Dostępność", level=1)
        if acc.get("avg_score") is not None:
            doc.add_paragraph(f"Średnia ocena dostępności: {acc['avg_score']}/5")
        for f in (acc.get("findings") or [])[:12]:
            doc.add_heading(f.get("category", ""), level=2)
            _add_caption(doc, str(f.get("severity", "")))
            if f.get("detail"):
                doc.add_paragraph(str(f["detail"]))
            if f.get("recommendation"):
                p = doc.add_paragraph()
                p.add_run("Rekomendacja: ").bold = True
                p.add_run(str(f["recommendation"]))

    # ── Devices ──────────────────────────────────────────────────────────
    devices = (stats.get("viewport") or {}).get("devices") or []
    if devices:
        doc.add_heading("Zachowanie na urządzeniach", level=1)
        table = doc.add_table(rows=1, cols=5)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        for i, text in enumerate(["Ekran", "Strony", "Przewijanie poziome",
                                  "Małe cele dotykowe", "Błędy kontrastu"]):
            hdr[i].text = text
        for d in devices:
            cells = table.add_row().cells
            cells[0].text = str(d.get("viewport", ""))
            cells[1].text = str(d.get("pages_scanned", 0))
            cells[2].text = str(d.get("pages_with_overflow", 0))
            cells[3].text = str(d.get("tap_targets_below_min", 0))
            cells[4].text = str(d.get("contrast_failures", 0))
        # Same wording as the HTML: the Word file is the version a client
        # edits and forwards, so it must not carry a claim the HTML retracted.
        from .reporter_html import _session_device_phrase
        _add_caption(doc,
                     "Pomiar techniczny dla każdej szerokości ekranu osobno. "
                     "Przejście przez serwis odbyło się "
                     f"{_session_device_phrase(stats)}.")

    # ── Methodology ──────────────────────────────────────────────────────
    if narr.get("methodology_note"):
        doc.add_heading("Nota metodologiczna", level=1)
        doc.add_paragraph(str(narr["methodology_note"]))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def ensure_report_docx(run_dir: Path) -> Path | None:
    """Build (or rebuild) the .docx for a run. None when there is no report.

    Rebuilt whenever aggregated_report.json is newer than the document, mirroring
    ensure_report_pdf — a re-rendered report must not be served stale.
    """
    import json

    run_dir = Path(run_dir)
    src = run_dir / "aggregated_report.json"
    if not src.is_file():
        return None
    out = run_dir / "aggregated_report.docx"
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    try:
        agg = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        log.exception("[docx] cannot read %s", src)
        return None
    return build_docx(agg, run_dir, out)
