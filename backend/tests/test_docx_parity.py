"""The Word file is the same report as the HTML, not a shorter one.

build_docx promised "the three formats are recognisably the same document" and
left out PageSpeed, scroll depth, broken links, the form funnel, the survey,
responsiveness and the statistics table in every archived run — and the caveat
that participants' own accounts are not checked against the trace, so their
blockers read as fact in the copy clients edit and forward.
"""
from docx import Document

from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter_html import OBJECTIVES_NOTE, SELF_REPORTED_CAVEAT

AGG = {
    "website": "x.pl", "task": "Zadanie",
    "narrative": {"executive_summary": "S.", "overall_ux_score": 3,
                  "critical_issues": [], "serious_issues": [], "minor_issues": [],
                  "priority_recommendations": [],
                  "responsiveness_assessment": "Ocena responsywności."},
    "pagespeed": {"url": "https://x.pl/",
                  "mobile": {"error": "The read operation timed out"},
                  "desktop": {"performance_score": 38, "performance_label": "poor",
                              "lcp": {"display": "5,2 s"}}},
    "scroll": {"avg_max_depth_pct": 22.0, "min_max_depth_pct": 10, "max_max_depth_pct": 40,
               "sessions_with_depth_pct": 2, "steps_measured": 4, "steps_unmeasurable": 2},
    "links": {"total_links_checked": 40, "broken_urls": ["https://x.pl/404"],
              "sessions_with_broken_links": 1},
    "forms": {"total_sessions": 2, "sessions_engaged_form": 1, "sessions_submitted_form": 0},
    "survey_summaries": {"q1": "Bo było prosto."},
    "statistics": {
        "participant_count": 2, "heuristic": {},
        "survey": {"questions": [
            {"id": "q1", "kind": "scale", "prompt": "Łatwo było?", "avg": 4.0, "n": 2,
             "min": 1, "max": 5, "distribution": [{"value": 4, "count": 2}], "not_assessable": 1},
            {"id": "q2", "kind": "not_assessable", "prompt": "Płatność?", "not_assessable": 2},
            {"id": "q3", "kind": "open", "prompt": "Czego brakowało?", "answers": ["Cennika"]},
        ]},
        "objective_reflections": [{"persona": "Marta", "outcome": "partial", "summary": "Relacja.",
                                   "self_reported_only": True}],
        "viewport": {"mobile_overflow_sessions": 1, "tablet_overflow_sessions": 0,
                     "devices": [{"viewport": "mobile", "pages_scanned": 3}]},
    },
}


def _doc(tmp_path):
    d = Document(build_docx(AGG, tmp_path, tmp_path / "r.docx"))
    heads = [p.text for p in d.paragraphs if p.style.name == "Heading 1"]
    text = "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(
        c.text for t in d.tables for row in t.rows for c in row.cells)
    return heads, text


def test_every_measurement_section_is_there(tmp_path):
    heads, _ = _doc(tmp_path)
    for h in ["Szybkość strony (PageSpeed Insights)", "Głębokość przewijania", "Uszkodzone linki",
              "Lejek formularzy", "Wyniki ankiety", "Responsywność", "Statystyki zbiorcze"]:
        assert h in heads, h


def test_the_content_matches_the_html_wording(tmp_path):
    _, text = _doc(tmp_path)
    assert "Pomiar nie powiódł się" in text and "38/100 (Słaby)" in text and "LCP 5,2 s" in text
    assert "Zmierzono na 4 z 6 kroków" in text and "rzadko przewijali" in text
    assert "https://x.pl/404" in text
    assert "Wysłali formularz" in text and "0/2" in text
    assert "śr. 4,00 (n=2" in text and "1 bez oceny" in text and "Brak ocen" in text
    assert "„Cennika”" in text and "Dlaczego tak odpowiadali: Bo było prosto." in text
    assert "1/2 sesji" in text and "nie zmierzono" in text, "tablet was not scanned"
    assert "Ocena responsywności." in text


def test_participants_accounts_carry_their_caveats(tmp_path):
    _, text = _doc(tmp_path)
    assert OBJECTIVES_NOTE in text
    assert SELF_REPORTED_CAVEAT in text


# --- what failed is said in both formats --------------------------------------

import copy  # noqa: E402

from simulated_web_agent.main.reporter_html import generate_html_report  # noqa: E402


def _failed_agg(**stats_over):
    agg = copy.deepcopy(AGG)
    agg["statistics"].update({
        "participant_count": 3,
        "audit_failures": [{"persona": "Ala", "persona_dir": "p0", "passes": ["analiza dostępności"],
                            "pass_ids": ["accessibility"]}] * 3,
        "accessibility": {"avg_score": None, "scored": 0, "of": 3},
        "first_impression": {"avg_score": None, "scored": 0, "of": 3},
        "survey": {"questions": [], "surveys_included": 0,
                   "failed_surveys": [{"persona_dir": f"p{i}", "persona": f"P{i}"} for i in range(3)]},
    })
    agg["statistics"].update(stats_over)
    return agg


def _both(agg, tmp_path):
    html = generate_html_report(agg)
    d = Document(build_docx(agg, tmp_path, tmp_path / "r.docx"))
    heads = [p.text for p in d.paragraphs if p.style.name == "Heading 1"]
    text = "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(
        c.text for t in d.tables for row in t.rows for c in row.cells)
    return html, heads, text


def test_an_accessibility_pass_nobody_has_a_score_for_is_in_the_word_file(tmp_path):
    """The Word file left the section out when there was nothing to average;
    the HTML printed it. Both say "nie oceniono" and why."""
    html, heads, text = _both(_failed_agg(), tmp_path)
    for name, body in (("html", html), ("docx", text)):
        assert "Nie oceniono — brak wyniku dla żadnego z 3 uczestników" in body, name
        assert "analiza nie powiodła się dla 3 z 3 uczestników" in body, name
    assert "Dostępność" in heads and "Pierwsze wrażenie (test 5 sekund)" in heads
    assert "nie oceniono" in text, "the statistics table row"


def test_a_partial_accessibility_score_says_how_many_it_covers_in_both(tmp_path):
    agg = _failed_agg(accessibility={"avg_score": 3.5, "scored": 2, "of": 3},
                      audit_failures=[{"persona": "Ala", "persona_dir": "p0", "passes": ["x"],
                                       "pass_ids": ["accessibility"]}])
    html, _, text = _both(agg, tmp_path)
    for name, body in (("html", html), ("docx", text)):
        assert "Ocena z 2 z 3 uczestników; analiza nie powiodła się dla 1 z 3" in body, name
        assert "3,50" in body, name


def test_every_survey_failing_still_leaves_a_survey_section_with_the_note(tmp_path):
    html, heads, text = _both(_failed_agg(), tmp_path)
    assert "Wyniki ankiety" in heads and "Wyniki ankiety" in html
    for name, body in (("html", html), ("docx", text)):
        assert "Ankiety nie powiodły się dla: P0, P1, P2" in body, name
        assert "(n=0 z 3)" in body, name


def test_a_link_check_that_opened_no_page_is_a_section_not_a_silence(tmp_path):
    agg = _failed_agg()
    agg["links"] = {"total_links_checked": 0, "broken_urls": [], "pages_failed": 2, "pages_total": 2}
    html, heads, text = _both(agg, tmp_path)
    assert "Uszkodzone linki" in heads
    for name, body in (("html", html), ("docx", text)):
        assert "(stron: 2 z 2; usterka narzędzia)" in body, name
        assert "nie jest wynikiem" in body, name


def test_pagespeed_with_no_score_reads_as_not_measured_in_both(tmp_path):
    agg = _failed_agg()
    agg["pagespeed"] = {"url": "https://x.pl/", "mobile": {"performance_score": None},
                        "desktop": {"error": "HTTP Error 429", "error_kind": "quota"}}
    html, _, text = _both(agg, tmp_path)
    for name, body in (("html", html), ("docx", text)):
        assert "zwróciła odpowiedź bez wyniku" in body, name
        assert "odrzuciła zapytanie (limit zapytań)" in body, name
        assert "nie wada strony" in body, name
    assert "None/100" not in html and "None/100" not in text
