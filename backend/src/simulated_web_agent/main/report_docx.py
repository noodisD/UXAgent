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

from .textutil import clip_words, decimal_text, pl_plural, sessions_in

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
    "unknown": "Wynik nieustalony",
    "persona_abandoned": "Sesja przerwana",
}

_FI_DIM_LABELS = {
    "purpose_clarity": "Jasność celu",
    "navigation_visibility": "Widoczność nawigacji",
    "call_to_action": "Wezwanie do działania",
    "visual_hierarchy": "Hierarchia wizualna",
    "trustworthiness": "Wiarygodność",
}


def avg_text(value) -> str:
    # Imported late like the other shared helpers from reporter_html.
    from .reporter_html import avg_text as _avg_text
    return _avg_text(value)


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


def _add_measurement_sections(doc, agg: dict) -> None:
    """PageSpeed, scroll depth, links and forms — the HTML's measurement sections.

    The Word file had none of them, although its docstring promised the same
    document as the HTML: a client editing the .docx never saw the PageSpeed
    result or the form funnel. Same fields, same wording (shared constants).
    """
    from .reporter_html import (FORM_FUNNEL_NOTE, PAGESPEED_LABELS, SCROLL_LOW_NOTE,
                                SCROLL_UNMEASURABLE, _SCROLL_METHOD, pagespeed_error_text,
                                scroll_coverage_text)

    ps = agg.get("pagespeed") or {}
    if ps and not ps.get("skipped") and not ps.get("error"):
        doc.add_heading("Szybkość strony (PageSpeed Insights)", level=1)
        rows = []
        for strategy in ("mobile", "desktop"):
            data = ps.get(strategy) or {}
            if data.get("error"):
                rows.append((strategy.capitalize(), pagespeed_error_text(data)))
                continue
            if data.get("performance_score") is None:
                continue
            parts = [f"{data['performance_score']}/100 "
                     f"({PAGESPEED_LABELS.get(data.get('performance_label', ''), '—')})"]
            for name, key in (("LCP", "lcp"), ("FCP", "fcp"), ("Speed Index", "speed_index"),
                              ("TBT", "tbt"), ("CLS", "cls")):
                shown = (data.get(key) or {}).get("display")
                if shown:
                    parts.append(f"{name} {shown}")
            rows.append((strategy.capitalize(), " · ".join(parts)))
        _add_kv_table(doc, rows)
        if ps.get("url"):
            _add_caption(doc, f"Źródło: Google PageSpeed Insights · {ps['url']}")

    scroll = agg.get("scroll") or {}
    if scroll:
        avg = scroll.get("avg_max_depth_pct")
        unmeasurable = int(scroll.get("steps_unmeasurable") or 0)
        if avg is not None or unmeasurable:
            doc.add_heading("Głębokość przewijania", level=1)
            if avg is None:
                doc.add_paragraph(SCROLL_UNMEASURABLE)
            else:
                from .reporter_html import pct_text, pl_plural
                k = scroll.get("sessions_with_depth_pct")
                sessions = (f"{k} {pl_plural(k, 'sesja', 'sesje', 'sesji')}"
                            if isinstance(k, int) else "— sesji")
                doc.add_paragraph(
                    f"Średnia maksymalna głębokość przewijania: {pct_text(avg)}% (zakres "
                    f"{pct_text(scroll.get('min_max_depth_pct', '—'))}–"
                    f"{pct_text(scroll.get('max_max_depth_pct', '—'))}%, {sessions})")
                if avg < 30:
                    doc.add_paragraph(SCROLL_LOW_NOTE)
            _add_caption(doc, f"{scroll_coverage_text(scroll)} {_SCROLL_METHOD}".strip())

    links = agg.get("links") or {}
    if links.get("total_links_checked"):
        doc.add_heading("Uszkodzone linki", level=1)
        broken = links.get("broken_urls") or []
        doc.add_paragraph(f"Sprawdzono {links['total_links_checked']} linków na odwiedzonych stronach.")
        from .reporter_html import unchecked_links_text
        if not broken:
            doc.add_paragraph("Nie wykryto uszkodzonych linków.")
        else:
            doc.add_paragraph(f"{len(broken)} {pl_plural(len(broken), 'uszkodzony link', 'uszkodzone linki', 'uszkodzonych linków')} "
                              f"{sessions_in(links.get('sessions_with_broken_links', 0))}:")
            for u in broken[:10]:
                doc.add_paragraph(str(u), style="List Bullet")
            if len(broken) > 10:
                _add_caption(doc, f"…i {len(broken) - 10} więcej")
        if unchecked_links_text(links):
            _add_caption(doc, unchecked_links_text(links))

    forms = agg.get("forms") or {}
    if forms and forms.get("sessions_engaged_form") is not None:
        doc.add_heading("Lejek formularzy", level=1)
        n = forms.get("total_sessions", 0)
        _add_kv_table(doc, [
            ("Wszyscy uczestnicy", f"{n}/{n}"),
            ("Wpisali coś w pole (także wyszukiwarkę)", f"{forms.get('sessions_engaged_form', 0)}/{n}"),
            ("Wysłali formularz", f"{forms.get('sessions_submitted_form', 0)}/{n}"),
        ])
        if forms.get("avg_fields_filled") is not None:
            doc.add_paragraph(f"Średnio {avg_text(forms['avg_fields_filled'])} pola na sesję.")
        _add_caption(doc, FORM_FUNNEL_NOTE)


def _add_survey_section(doc, stats: dict, summaries: dict) -> None:
    """The survey, question by question, as the HTML shows it."""
    from .reporter_html import REVERSED_TAG, survey_notes

    questions = [q for q in ((stats.get("survey") or {}).get("questions") or [])
                 if isinstance(q, dict)]
    notes = survey_notes(stats)
    if not questions and not notes:
        return
    doc.add_heading("Wyniki ankiety", level=1)
    for note in notes:
        doc.add_paragraph(note)
    for q in questions:
        p = doc.add_paragraph()
        p.add_run(str(q.get("prompt") or q.get("id") or "")).bold = True
        kind = q.get("kind")
        na = int(q.get("not_assessable") or 0)
        if kind == "open":
            for a in (q.get("answers") or [])[:8]:
                doc.add_paragraph(f"„{a}”", style="List Bullet")
        elif kind == "not_assessable":
            _add_caption(doc, f"Brak ocen — żaden uczestnik nie mógł odpowiedzieć ({na}): "
                              "pytanie dotyczy etapu, do którego sesje nie dotarły.")
        else:
            dist = ", ".join(f"{d.get('value')}: {d.get('count')}"
                             for d in (q.get("distribution") or []) if d.get("count"))
            line = (f"śr. {avg_text(q.get('avg'))} (n={q.get('n', 0)}, "
                    f"skala {q.get('min', 1)}–{q.get('max', 5)})")
            if dist:
                line += f" · rozkład {dist}"
            if na:
                line += f" · {na} bez oceny (pytanie nie dotyczyło przebiegu sesji)"
            if q.get("reverse"):
                line += f" · {REVERSED_TAG}"
            doc.add_paragraph(line)
        why = summaries.get(q.get("id"))
        if why:
            _add_caption(doc, f"Dlaczego tak odpowiadali: {why}")


def build_docx(agg: dict, run_dir: Path, out_path: Path) -> Path:
    """Write the aggregated report to `out_path` as .docx and return the path."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    from .reporter import HEURISTIC_NAMES, resolve_narrative

    stats = agg.get("statistics") or {}
    narr = resolve_narrative(agg)
    doc = Document()

    # ── Logo ─────────────────────────────────────────────────────────────
    # Red on the white page, matching the site name's accent below it.
    from .branding import logo_path
    logo = logo_path("red")
    if logo is not None:
        try:
            doc.add_picture(str(logo), width=Cm(3.6))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.LEFT
        except Exception as e:  # a bad image must not cost the report
            log.warning("[docx] could not add the logo: %s", e)

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
        ("Ocena ogólna", f"{decimal_text(score, 1) if isinstance(score, (int, float)) else score}/5"
                         if score is not None else "—"),
        # The rendered issue lists, as in the HTML: counting only verified
        # Nielsen findings disagreed with the cards that follow.
        ("Problemy krytyczne", len(narr.get("critical_issues") or [])),
        ("Problemy poważne", len(narr.get("serious_issues") or [])),
        ("Problemy drobne", len(narr.get("minor_issues") or [])),
        ("Do weryfikacji przez człowieka", (stats.get("heuristic") or {}).get(
            "severity_counts", {}).get("needs_verification", 0)),
    ])
    from .reporter_html import audit_failures_text
    if audit_failures_text(stats):
        _add_caption(doc, audit_failures_text(stats))
    excluded = [e for e in (stats.get("excluded_sessions") or []) if isinstance(e, dict)]
    if excluded:
        total = int(stats.get("participant_count") or 0) + len(excluded)
        _add_caption(doc, f"Pominięto {len(excluded)} z {total} sesji, które nie zobaczyły "
                          "badanej strony, więc niczego o niej nie mówią — " + "; ".join(
                              f"{e.get('persona') or '—'}: {e.get('reason') or ''}" for e in excluded) + ".")

    # ── Summary ──────────────────────────────────────────────────────────
    if narr.get("executive_summary"):
        doc.add_heading("Podsumowanie", level=1)
        doc.add_paragraph(str(narr["executive_summary"]))
        if narr.get("score_rationale"):
            _add_caption(doc, str(narr["score_rationale"]))

    # ── Heuristic legend ─────────────────────────────────────────────────
    # First, as Karolina asked, and before the score breakdown that cites the
    # heuristics by number. "nie stwierdzono naruszeń" only where nothing was
    # raised at all: a heuristic with findings held for a human says so.
    by_h = {h.get("heuristic_id"): h
            for h in ((stats.get("heuristic") or {}).get("by_heuristic") or [])}
    held_back = {int(k): v for k, v in
                 ((stats.get("heuristic") or {}).get("excluded_by_heuristic") or {}).items()
                 if str(k).lstrip("-").isdigit()}
    doc.add_heading("Heurystyki Nielsena — zakres oceny", level=1)
    doc.add_paragraph(
        "Ocena opiera się na dziesięciu heurystykach użyteczności Nielsena. "
        "Każdy problem użyteczności w raporcie jest przypisany do jednej z nich (H1–H10); "
        "bariery dostępności i wydajności są opisane osobno."
    )
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "", "Heurystyka", "Wynik"
    # Same rule as the HTML: nothing observed is not "no violation".
    from .reporter_html import _anything_observed
    observed = _anything_observed(stats)
    for h_id in sorted(HEURISTIC_NAMES):
        hit = by_h.get(h_id)
        pending = held_back.get(h_id, 0)
        note = f" · {pending} do weryfikacji" if pending else ""
        cells = table.add_row().cells
        cells[0].text = f"H{h_id}"
        cells[1].text = HEURISTIC_NAMES[h_id]
        if hit:
            from .reporter_html import devices_text
            dev = devices_text(hit.get("devices"))
            cells[2].text = (f"{hit.get('worst_severity', '')} · {hit.get('sessions_affected', '')}"
                             f"{' · ' + dev if dev else ''}{note}")
        elif pending:
            cells[2].text = f"brak potwierdzonych naruszeń{note}"
        elif not observed:
            cells[2].text = "nie oceniono"
        else:
            cells[2].text = "nie stwierdzono naruszeń"

    # ── How the score was computed ───────────────────────────────────────
    # Same breakdown as the HTML. This is the copy that gets edited and
    # forwarded, so it must carry the arithmetic, not just the number.
    breakdown = stats.get("score_breakdown") or {}
    if breakdown.get("score") is not None:
        doc.add_heading("Jak obliczono ocenę", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for i, text in enumerate(["Pozycja", "Uzasadnienie", "Punkty"]):
            table.rows[0].cells[i].text = text

        def _row(a, b, c):
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = a, b, c

        _row("Punkt wyjścia", "", f"{decimal_text(breakdown['start'], 1)}")
        for d in breakdown.get("deductions") or []:
            amt = d.get("amount", 0)
            _row(str(d.get("label", "")), str(d.get("basis", "")),
                 f"{'−' if amt >= 0 else '+'}{decimal_text(abs(amt), 2)}")
        _row("Suma odliczeń", "", f"−{decimal_text(breakdown['total_deduction'], 2)}")
        _row("Ocena końcowa",
             f"{decimal_text(breakdown['start'], 1)} − {decimal_text(breakdown['total_deduction'], 2)} = "
             f"{decimal_text(breakdown['raw'], 2)}, zaokrąglone"
             + (" (minimum 1,0)" if breakdown.get("floored") else ""),
             f"{decimal_text(breakdown['score'], 1)}")
        for n in breakdown.get("not_counted") or []:
            _add_caption(doc, f"Nie wliczono: {n.get('label')} — {n.get('reason')}")

    # ── Task outcomes ────────────────────────────────────────────────────
    reflections = stats.get("objective_reflections") or []
    if reflections:
        doc.add_heading("Realizacja zadania", level=1)
        from .reporter_html import (OBJECTIVES_NOTE, SELF_REPORTED_CAVEAT, SESSION_ERROR_NOTE,
                                    _SOURCE_PL, held_claims_note, objective_outcome)
        # The HTML's standing caveat: these are the participants' own accounts,
        # never checked against the trace. Without it the blockers read as fact.
        _add_caption(doc, OBJECTIVES_NOTE)
        goals = {str(r.get("objective") or "") for r in reflections}
        shared_goal = len(goals) == 1 and "" not in goals
        if shared_goal:
            _add_caption(doc, f"Cel: {next(iter(goals))}")
        for r in reflections:
            outcome = objective_outcome(r)
            doc.add_heading(
                f"{r.get('persona', '—')} — {_OUTCOME_LABEL.get(outcome, outcome or '—')}",
                level=2,
            )
            src = _SOURCE_PL.get(r.get("outcome_source") or "")
            declared = r.get("outcome") or ""
            if src and r.get("outcome_source") != "declared" and declared and declared != outcome:
                src += f"; model ocenił: {_OUTCOME_LABEL.get(declared, declared).lower()}"
            if src:
                _add_caption(doc, src)
            if r.get("self_reported_only"):
                _add_caption(doc, SELF_REPORTED_CAVEAT)
            else:
                held = held_claims_note(stats, r.get("persona"))
                if held:
                    _add_caption(doc, held)
            if r.get("session_error"):
                _add_caption(doc, SESSION_ERROR_NOTE.format(steps=r["session_error"].get("steps", "?")))
            if r.get("objective") and not shared_goal:
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

    # ── Issues, with their evidence ──────────────────────────────────────
    from .reporter_html import DEVICES_NOTE, devices_text as _devices_text
    if any(_devices_text(i.get("devices")) for _h, _l in _issues(narr)
           for i in _l if isinstance(i, dict)):
        doc.add_heading("Problemy a urządzenia", level=1)
        doc.add_paragraph(DEVICES_NOTE)
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
            from .reporter_html import devices_text
            dev = devices_text(issue.get("devices"))
            if dev:
                meta.append(f"na urządzeniach: {dev}")
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
                from .scoring import alt_text_measured_text
                p = doc.add_paragraph()
                p.add_run(measured.get("text") or alt_text_measured_text(measured)).bold = True
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
            if issue.get("route_caveat"):
                _add_caption(doc, str(issue["route_caveat"]))
            for ex in (issue.get("examples") or [])[:2]:
                caption = " · ".join(x for x in [
                    ex.get("persona_name"),
                    f"krok {ex['step']}" if ex.get("step") is not None else "",
                    ex.get("url"),
                ] if x)
                # A missing screenshot no longer drops the citation with it.
                if not _add_picture(doc, run_dir, ex.get("screenshot"), caption):
                    _add_caption(doc, caption)
                from .reporter_html import evidence_quote
                label, quote = evidence_quote(ex)
                if quote:
                    _add_caption(doc, f"{label}: „{quote}”")
            others = [o for o in (issue.get("other_observations") or []) if isinstance(o, dict)]
            if others:
                _add_caption(doc, "Inne obserwacje w tej heurystyce (inna wada niż opisana powyżej):")
                for o in others:
                    _add_caption(doc, f"{o.get('persona_name', '')}, krok {o.get('step')} — "
                                      f"„{clip_words(o.get('evidence', ''), 160)}”")

    # ── Held for a human ─────────────────────────────────────────────────
    held_items = (stats.get("heuristic") or {}).get("needs_verification_items") or []
    from .reporter_html import _unconfirmed_note
    unconfirmed = _unconfirmed_note(narr)
    obstacles = [o for o in (stats.get("repeated_obstacles") or []) if isinstance(o, dict)]
    recurring = [t for t in (stats.get("recurring_reports") or []) if isinstance(t, dict)]
    conflicts = [c for c in (stats.get("route_conflicts") or []) if isinstance(c, dict)]
    if held_items or unconfirmed or obstacles or recurring or conflicts:
        doc.add_heading("Do weryfikacji przez człowieka", level=1)
        if held_items:
            from .reporter_html import held_intro_text
            doc.add_paragraph(held_intro_text(len(held_items)))
        if unconfirmed:
            doc.add_paragraph(unconfirmed)
        for it in held_items:
            h_id = it.get("heuristic_id")
            title = (f"H{h_id}: {it.get('heuristic_name', '')}" if isinstance(h_id, int) and h_id
                     else it.get("heuristic_name") or "Wniosek")
            doc.add_heading(title, level=2)
            _add_caption(doc, f"zgłoszone jako {it.get('severity_claimed', '')} · "
                              f"{it.get('persona_name', '')}, krok {it.get('step')}")
            doc.add_paragraph(f"„{clip_words(it.get('evidence', ''), 300)}”")
            p = doc.add_paragraph()
            p.add_run("Dlaczego wstrzymane: ").bold = True
            p.add_run(str(it.get("reason", "")))
            seen = str(it.get("verifier_reason") or "").strip()
            if seen:
                p = doc.add_paragraph()
                p.add_run("Co pokazuje zapis sesji: ").bold = True
                p.add_run(seen)
            p = doc.add_paragraph()
            p.add_run("Co sprawdzić: ").bold = True
            p.add_run(str(it.get("check", "")))
            _add_picture(doc, run_dir, it.get("screenshot"), f"krok {it.get('step')}")
        if recurring:
            from .reporter_html import RECURRING_INTRO, theme_text
            doc.add_heading("Opisywane przez wielu uczestników", level=2)
            doc.add_paragraph(RECURRING_INTRO)
            for t in recurring:
                doc.add_paragraph(theme_text(t), style="List Bullet")
        if conflicts:
            from .conflicts import INTRO as CONFLICTS_INTRO, conflict_text
            doc.add_heading("Rozbieżne relacje uczestników", level=2)
            doc.add_paragraph(CONFLICTS_INTRO)
            for c in conflicts:
                doc.add_paragraph(conflict_text(c), style="List Bullet")
        if obstacles:
            from .reporter_html import OBSTACLES_INTRO, obstacle_text
            doc.add_heading("Powtarzające się przeszkody w klikaniu", level=2)
            doc.add_paragraph(OBSTACLES_INTRO)
            for o in obstacles:
                doc.add_paragraph(obstacle_text(o), style="List Bullet")

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
            if r.get("route_caveat"):
                action += chr(10) + str(r["route_caveat"])
            cells[2].text = action

    _add_measurement_sections(doc, agg)

    # ── First impression ─────────────────────────────────────────────────
    fi = stats.get("first_impression") or {}
    if fi.get("avg_score") is not None or fi.get("dimension_avgs"):
        doc.add_heading("Pierwsze wrażenie (test 5 sekund)", level=1)
        if fi.get("avg_score") is not None:
            doc.add_paragraph(f"Średnia ocena: {avg_text(fi['avg_score'])}/5")
        for s in (fi.get("screenshots") or [])[:3]:
            caption = str(s.get("persona") or "")
            if s.get("score") is not None:
                caption = f"{caption} · {avg_text(s['score'])}/5".lstrip(" ·")
            _add_picture(doc, run_dir, s.get("screenshot"), caption)
        dims = [(label, (fi.get("dimension_avgs") or {}).get(key))
                for key, label in _FI_DIM_LABELS.items()
                if (fi.get("dimension_avgs") or {}).get(key) is not None]
        _add_kv_table(doc, [(l, f"{avg_text(v)}/5") for l, v in dims])
        if narr.get("first_impression_assessment"):
            doc.add_paragraph(str(narr["first_impression_assessment"]))

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
                    if i.get("change"):
                        fix = doc.add_paragraph(style="List Bullet 2")
                        fix.add_run("Propozycja zmiany: ").bold = True
                        fix.add_run(str(i["change"]))

    _add_survey_section(doc, stats, agg.get("survey_summaries") or {})

    # ── Accessibility ────────────────────────────────────────────────────
    acc = stats.get("accessibility") or {}
    if acc.get("avg_score") is not None or acc.get("findings"):
        doc.add_heading("Dostępność", level=1)
        if acc.get("avg_score") is not None:
            doc.add_paragraph(f"Średnia ocena dostępności: {avg_text(acc['avg_score'])}/5")
        if narr.get("accessibility_assessment"):
            doc.add_paragraph(str(narr["accessibility_assessment"]))
        from .scoring import accessibility_severity

        images = (acc.get("measured") or {}).get("images") or {}
        for f in (acc.get("findings") or [])[:12]:
            doc.add_heading(f.get("category", ""), level=2)
            # The HTML's severity, not the raw one: this file printed alt text
            # "poważny" where the HTML, the PDF and the score said "drobny".
            sev, measured = accessibility_severity(f, images)
            _add_caption(doc, sev)
            if f.get("detail"):
                doc.add_paragraph(str(f["detail"]))
            if measured:
                doc.add_paragraph(measured)
            for o in (f.get("other_instances") or [])[:3]:
                p = doc.add_paragraph()
                p.add_run("Również: ").bold = True
                from .textutil import display_url
                p.add_run(f"{o.get('detail') or ''} ({display_url(o.get('url') or '')})")
            if f.get("recommendation"):
                p = doc.add_paragraph()
                p.add_run("Rekomendacja: ").bold = True
                p.add_run(str(f["recommendation"]))

    # ── Responsiveness ───────────────────────────────────────────────────
    from .reporter_html import stats_table_rows
    rows = dict(stats_table_rows(stats, narr))
    doc.add_heading("Responsywność", level=1)
    _add_kv_table(doc, [("Mobile — przewijanie poziome", rows["Overflow mobile"]),
                        ("Tablet — przewijanie poziome", rows["Overflow tablet"])])
    if narr.get("responsiveness_assessment"):
        doc.add_paragraph(str(narr["responsiveness_assessment"]))

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

    # ── Statistics ───────────────────────────────────────────────────────
    doc.add_heading("Statystyki zbiorcze", level=1)
    _add_kv_table(doc, stats_table_rows(stats, narr))

    # ── Methodology ──────────────────────────────────────────────────────
    if narr.get("methodology_note"):
        doc.add_heading("Nota metodologiczna", level=1)
        doc.add_paragraph(str(narr["methodology_note"]))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def ensure_report_docx(run_dir: Path) -> Path | None:
    """Build the .docx for a run from its JSON. None when there is no report.

    Built on every request (0.1-0.3 s). It used to be cached until the JSON
    changed, so a document built before a renderer fix was served for ever: the
    2026-09-24 hyundai .docx still had no "Problemy drobne" row and its
    heuristic list in the old place, while the HTML beside it had both.
    """
    import json

    run_dir = Path(run_dir)
    src = run_dir / "aggregated_report.json"
    if not src.is_file():
        return None
    out = run_dir / "aggregated_report.docx"
    try:
        agg = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        log.exception("[docx] cannot read %s", src)
        return None
    return build_docx(agg, run_dir, out)
