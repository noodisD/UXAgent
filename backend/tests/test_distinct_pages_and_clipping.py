"""Page counts are distinct pages, and long text is cut at a word.

The 2026-09-30 hyundai report said "16 z 30 zeskanowanych podstron": three
sessions of five pages each, every scan visiting the same five. It also printed
lines cut mid-word ("…ni (https://…)") and 300-character addresses made of a
configurator share-code."""
from docx import Document

from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter import _order_a11y_instances, aggregate_statistics
from simulated_web_agent.main.reporter_html import generate_html_report
from simulated_web_agent.main.textutil import clip_words, display_url, view_key

LONG = ("https://www.hyundai.com/pl/pl/jazda-testowa.html?select-model-name=G4%7CS6%7C%7CE"
        "&configuratorLink=https://www.hyundai.com/pl/pl/modele/nowy-i30/konfigurator.html%23/share-code/FSC!G4S6")


def test_clip_words_cuts_at_a_word_with_an_ellipsis():
    text = "Na najgorszej stronie znaleziono przycisk który nie ma nazwy " * 10
    clipped = clip_words(text, 100)
    assert len(clipped) <= 101 and clipped.endswith("…")
    assert clipped[:-1].split()[-1] in text.split(), "no half word"
    assert clip_words("krótki", 100) == "krótki" and clip_words(None) == ""


def test_the_same_page_with_different_queries_is_one_page():
    a = view_key("https://www.hyundai.com/pl/pl/jazda-testowa.html?select-model-name=SW%7CS6")
    b = view_key("https://www.hyundai.com/pl/pl/jazda-testowa.html?select-model-name=G4%7CS6")
    assert a == b == "www.hyundai.com/pl/pl/jazda-testowa.html"


def test_a_single_page_apps_routes_are_different_views():
    base = "https://www.hyundai.com/pl/pl/modele/nowy-i20/konfigurator.html"
    assert view_key(base + "#/summary") != view_key(base + "#/interior")
    assert view_key(base + "#/summary") == "www.hyundai.com/pl/pl/modele/nowy-i20/konfigurator.html#/summary"
    assert view_key(base + "#section") == view_key(base), "a plain anchor is the same page"


def test_a_report_address_is_readable():
    shown = display_url(LONG)
    assert shown == "www.hyundai.com/pl/pl/jazda-testowa.html"
    assert "configuratorLink" not in shown and len(shown) < 90


def _viewport(url, overflow=False):
    return {"url": url, "viewport": "mobile", "horizontal_overflow": overflow,
            "tap_targets": {}, "contrast": {}, "small_fonts": [], "wide_elements": []}


def _session(name, urls):
    return {"trace_dir": f"runs/R/{name}", "persona": f"Persona: {name}", "first_impression": {},
            "heuristic_findings": [], "accessibility": {"findings": []},
            "viewport_findings": [_viewport(u, overflow=u.endswith("konfigurator.html")) for u in urls],
            "survey_result": {"answers": []}}


def test_three_sessions_over_the_same_pages_scan_those_pages_once():
    pages = ["https://x.pl/", "https://x.pl/konfigurator.html", "https://x.pl/jazda.html?model=1"]
    sessions = [_session(n, pages[:2] + [pages[2].replace("=1", f"={i}")]) for i, n in enumerate("ABC")]
    device = aggregate_statistics(sessions)["viewport"]["devices"][0]
    assert device["pages_scanned"] == 3, "not 9"
    assert device["pages_with_overflow"] == 1, "not 3"
    assert not any(k.startswith("_") for k in device), "working sets are stripped"


def test_an_instance_detail_is_cut_at_a_word():
    entry = {"_worst_url": "https://x.pl/a", "_instances": [
        {"severity": "poważny", "detail": "Pole bez nazwy.", "persona": "A",
         "example": {"url": "https://x.pl/a"}},
        {"severity": "drobny", "detail": "Na najgorszej stronie przycisk który nie ma nazwy " * 12,
         "persona": "B", "example": {"url": "https://x.pl/b"}},
    ]}
    _order_a11y_instances(entry)
    detail = entry["other_instances"][0]["detail"]
    assert detail.endswith("…") and len(detail) <= 301


AGG = {
    "website": "x.pl", "task": "Zadanie",
    "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                  "serious_issues": [], "minor_issues": [], "priority_recommendations": []},
    "statistics": {"participant_count": 1, "heuristic": {}, "accessibility": {
        "avg_score": 3, "measured": {}, "findings": [{
            "category": "Brak nazwy kontrolki", "severity": "poważny", "verified": True,
            "topic": "control_name", "detail": "Przycisk bez nazwy.", "recommendation": "Nadać nazwę.",
            "urls": [LONG, "https://x.pl/"], "personas": ["A"],
            "other_instances": [{"detail": "Pole wyboru bez nazwy.", "url": LONG, "persona": "B",
                                 "severity": "poważny"}]}]}},
}


def test_the_html_shows_readable_addresses():
    html = generate_html_report(AGG)
    assert "configuratorLink" not in html and "select-model-name" not in html
    assert "www.hyundai.com/pl/pl/jazda-testowa.html" in html


def test_the_word_file_shows_readable_addresses(tmp_path):
    doc = Document(build_docx(AGG, tmp_path, tmp_path / "r.docx"))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Również: Pole wyboru bez nazwy. (www.hyundai.com/pl/pl/jazda-testowa.html)" in text
    assert "configuratorLink" not in text
