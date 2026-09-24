"""Evidence must quote the page, and quote the part that proves the claim.

The report printed the first 400 characters of the perception model's
description of each page — in English, and always the page header — as if it
were a quotation. For "null kW (null KM)" it read "Close button. Navigation
links: Rok produkcji, … Page title: Wybie", cut off before the word null. The
page's own text was on disk the whole time.
"""
import json

from simulated_web_agent.main.evidence import page_quote, resolve_evidence
from simulated_web_agent.main.trace_store import TraceStore

PAGE = ("<html><body><nav>Rok produkcji Wersja wyposażenia Napęd</nav>"
        "<main><h2>Wybierz napęd</h2><div>Hybrydowy 1.6 GDI HEV 2WD "
        "<span>null kW (null KM)</span> 4.6 l/100km</div></main></body></html>")
OBSERVATION = ("Close button. Navigation links: Rok produkcji, Wersja wyposażenia, Napęd. "
               "Page title: Wybierz napęd. " + "Filler text. " * 40 +
               "The engine shows power as null kW (null KM).")
FINDING = {"heuristic_id": 9, "step": 0,
           "evidence": "Moc silnika jest wyświetlana jako 'null kW (null KM)'."}


def _store(tmp_path):
    store = TraceStore(tmp_path)
    store.write_action_trace([json.dumps({"action": "click", "target": "x"})])
    store.append_observation({"tabs": [{"url": "https://x.pl/", "is_active": True}]})
    store.write_observation_step_text(0, OBSERVATION)
    store.write_simp_html(0, PAGE)
    return store


def test_the_quote_is_the_pages_own_words_around_the_claim(tmp_path):
    quote = page_quote(_store(tmp_path), 0, FINDING["evidence"])
    assert "null kW (null KM)" in quote
    assert "Hybrydowy" in quote, "the surrounding Polish text, not a paraphrase"
    assert "Close button" not in quote


def test_a_finding_that_quotes_nothing_on_the_page_gets_no_page_quote(tmp_path):
    assert page_quote(_store(tmp_path), 0, "Strona jest nieczytelna.") == ""


def test_the_session_description_is_centred_on_the_claim_too(tmp_path):
    """The fallback, when there is no page quote, must still be about the claim
    rather than the page header."""
    enriched = resolve_evidence(_store(tmp_path), FINDING)
    assert "null kW" in enriched["observation_quote"]
    assert enriched["page_quote"]


def test_the_card_labels_what_kind_of_quote_it_is():
    from simulated_web_agent.main.reporter_html import _render_all_issues

    page = {"critical_issues": [{"heuristic_id": 9, "heuristic_name": "X", "verified": True,
            "examples": [{"page_quote": "null kW (null KM)", "observation_quote": "Close button",
                          "persona_name": "A", "step": 6, "url": "https://x.pl/"}]}]}
    html = _render_all_issues(page)
    assert "Tekst ze strony" in html and "Close button" not in html

    desc = {"critical_issues": [{"heuristic_id": 9, "heuristic_name": "X", "verified": True,
            "examples": [{"page_quote": "", "observation_quote": "The page shows X",
                          "persona_name": "A", "step": 6, "url": "https://x.pl/"}]}]}
    assert "Opis strony w zapisie sesji" in _render_all_issues(desc), \
        "an English model paraphrase must not be presented as a quotation"


def test_a_failed_pagespeed_measurement_is_not_shown_as_a_raw_exception():
    from simulated_web_agent.main.reporter_html import _render_pagespeed

    html = _render_pagespeed({"url": "https://x.pl/",
                              "mobile": {"error": "The read operation timed out"},
                              "desktop": {"error": "The read operation timed out"}})
    assert "The read operation timed out" not in html
    assert "nie wada strony" in html
