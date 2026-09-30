"""One decimal separator and correct Polish counts across the report formats.

The 2026-09-27 run printed "Średnia 3,07" beside a scroll depth of "22.0%" and
per-participant scores of "3.0/5", and the held-back list said "1 wniosków nie
trafiło"."""
from docx import Document

from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter_html import (
    generate_html_report, held_intro_text, pct_text, pl_plural,
)


def test_percentages_use_a_comma_and_one_decimal():
    assert pct_text(22.0) == "22,0"
    assert pct_text(33.333) == "33,3"
    assert pct_text(10) == "10,0"
    assert pct_text(None) == "—" and pct_text("—") == "—"
    assert pct_text(True) == "True", "a bool is not a number"


def test_polish_plural_forms():
    forms = ("wniosek", "wnioski", "wniosków")
    got = {n: pl_plural(n, *forms) for n in (0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 112)}
    assert got == {0: "wniosków", 1: "wniosek", 2: "wnioski", 4: "wnioski", 5: "wniosków",
                   11: "wniosków", 12: "wniosków", 14: "wniosków", 21: "wniosków",
                   22: "wnioski", 25: "wniosków", 112: "wniosków"}


def test_the_held_intro_is_right_for_one_and_for_many():
    for k in (1, 2, 5):
        text = held_intro_text(k)
        assert f"Wstrzymano wniosków: {k}." in text
        assert "nie trafiło" not in text and "1 wniosków" not in text


AGG = {
    "website": "x.pl", "task": "Zadanie",
    "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                  "serious_issues": [], "minor_issues": [], "priority_recommendations": []},
    "scroll": {"avg_max_depth_pct": 22.0, "min_max_depth_pct": 10, "max_max_depth_pct": 40,
               "sessions_with_depth_pct": 2, "steps_measured": 4, "steps_unmeasurable": 0},
    "forms": {"total_sessions": 2, "sessions_engaged_form": 1, "sessions_submitted_form": 0,
              "avg_fields_filled": 2.5},
    "statistics": {
        "participant_count": 2,
        "heuristic": {"needs_verification_items": [{"heuristic_id": 6, "step": 1,
                                                    "persona_name": "A", "evidence": "x"}]},
        "first_impression": {"avg_score": 3.07, "screenshots": [
            {"persona": "Kacper", "score": 3.0, "screenshot": "screenshot/landing.png"}]},
    },
}


def test_html_uses_one_separator_and_counts_correctly():
    html = generate_html_report(AGG)
    assert "22,0%" in html and "22.0%" not in html
    assert "Średnio 2,50 pól na sesję." in html
    assert "Wstrzymano wniosków: 1." in html
    assert "3,00/5" in html and "3.0/5" not in html


def test_word_uses_one_separator_and_counts_correctly(tmp_path):
    doc = Document(build_docx(AGG, tmp_path, tmp_path / "r.docx"))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Średnia maksymalna głębokość przewijania: 22,0% (zakres 10,0–40,0%, 2 sesje)" in text
    assert "Średnio 2,50 pól na sesję." in text
    assert "Wstrzymano wniosków: 1." in text
