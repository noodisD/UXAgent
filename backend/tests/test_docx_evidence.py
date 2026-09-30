"""The Word file cites its evidence the way the HTML does.

It embedded the screenshot and its URL and nothing else, so the page's own words
("Tekst ze strony") and the warning that a description is the session's own
automatic one were missing from the copy clients edit and forward; a missing
screenshot file also dropped the whole citation. Held-back items had no picture."""
from docx import Document

from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter_html import evidence_quote


def test_the_page_quote_wins_over_the_sessions_description():
    label, text = evidence_quote({"page_quote": "Wybierz wersję wyposażenia",
                                  "observation_quote": "- heading \"Wybierz\" [level=2]"})
    assert (label, text) == ("Tekst ze strony", "Wybierz wersję wyposażenia")


def test_without_a_page_quote_the_description_is_labelled_automatic():
    label, text = evidence_quote({"observation_quote": "Strona pokazuje cenę."})
    assert label == "Opis strony w zapisie sesji (automatyczny)" and text == "Strona pokazuje cenę."


def test_nothing_to_quote_is_empty():
    assert evidence_quote({})[1] == ""


def _agg(example):
    return {
        "website": "x.pl", "task": "Zadanie",
        "narrative": {"executive_summary": "S.", "overall_ux_score": 3,
                      "critical_issues": [{"heuristic_id": 2, "heuristic_name": "Zgodność z rzeczywistością",
                                           "impact": "Cena nie zgadza się z wybraną wersją.",
                                           "recommendation": "Pokazać cenę wybranej wersji."}],
                      "serious_issues": [], "minor_issues": [], "priority_recommendations": []},
        "statistics": {"participant_count": 1, "heuristic": {
            "by_heuristic": [{"heuristic_id": 2, "verified": True, "worst_severity": "krytyczny",
                              "examples": [example]}],
            "needs_verification_items": []}},
    }


def _text(tmp_path, example):
    doc = Document(build_docx(_agg(example), tmp_path, tmp_path / "r.docx"))
    return "\n".join(p.text for p in doc.paragraphs)


EX = {"persona_name": "Magdalena", "step": 3, "url": "https://x.pl/konfigurator",
      "screenshot": "screenshot/brak.png", "verified": True}


def test_the_quote_follows_the_citation_and_a_missing_screenshot_keeps_it(tmp_path):
    text = _text(tmp_path, {**EX, "page_quote": "Cena całkowita: 201 300,00 zł"})
    assert "Magdalena · krok 3 · https://x.pl/konfigurator" in text
    assert "Tekst ze strony: „Cena całkowita: 201 300,00 zł”" in text


def test_a_description_without_a_page_quote_is_labelled(tmp_path):
    text = _text(tmp_path, {**EX, "observation_quote": "Opis z zapisu."})
    assert "Opis strony w zapisie sesji (automatyczny): „Opis z zapisu.”" in text
    assert "Tekst ze strony" not in text
