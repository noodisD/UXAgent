"""A held-back finding says what the verifier saw, and its author's reflection
says what was held. The 2026-09-27 run held "Mandatory Field" as unsupported and
printed only "the record does not confirm it"; the same persona's blocker then
repeated the claim as fact."""
from docx import Document

from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter_html import generate_html_report, held_claims_note

HELD = {
    "heuristic_id": 6, "heuristic_name": "Rozpoznawanie zamiast przypominania",
    "severity_claimed": "drobny", "persona_name": "Magdalena", "step": 16,
    "evidence": "Etykieta „Mandatory Field” jest po angielsku",
    "reason": "zapis sesji nie potwierdza tego stwierdzenia",
    "verifier_reason": "Obserwacja wskazuje, że tekst jest dostępny tylko dla czytników ekranu.",
    "check": "Sprawdź na stronie, czy opisany problem występuje.",
}

AGG = {
    "website": "x.pl", "task": "Zadanie",
    "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                  "serious_issues": [], "minor_issues": [], "priority_recommendations": []},
    "statistics": {
        "participant_count": 2,
        "heuristic": {"needs_verification_items": [HELD]},
        "objective_reflections": [
            {"persona": "Magdalena", "outcome": "success", "summary": "Relacja.",
             "blockers": ["Etykieta „Mandatory Field” po angielsku"]},
            {"persona": "Dorota", "outcome": "partial", "summary": "Relacja."},
        ],
    },
}


def test_the_held_card_shows_what_the_verifier_read_in_the_session():
    html = generate_html_report(AGG)
    assert "Co pokazuje zapis sesji:" in html
    assert "dostępny tylko dla czytników ekranu" in html


def test_a_held_item_without_a_reason_shows_no_empty_line():
    bare = {**HELD, "verifier_reason": ""}
    agg = {**AGG, "statistics": {**AGG["statistics"], "heuristic": {"needs_verification_items": [bare]}}}
    assert "Co pokazuje zapis sesji:" not in generate_html_report(agg)


def test_the_persona_whose_claim_was_held_is_told_so_on_their_reflection():
    note = held_claims_note(AGG["statistics"], "Magdalena")
    assert "krok 16" in note and "Mandatory Field" in note and "niepotwierdzone" in note
    assert held_claims_note(AGG["statistics"], "Dorota") == ""
    assert held_claims_note(AGG["statistics"], None) == ""


def test_only_that_reflection_card_carries_the_note():
    html = generate_html_report(AGG)
    assert html.count("Wnioski tej osoby wstrzymane po weryfikacji") == 1


def test_the_word_file_has_both_lines(tmp_path):
    doc = Document(build_docx(AGG, tmp_path, tmp_path / "r.docx"))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Co pokazuje zapis sesji: Obserwacja wskazuje" in text
    assert text.count("Wnioski tej osoby wstrzymane po weryfikacji") == 1
