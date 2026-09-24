"""A form is submitted by a submit control, not by a word in a link's name.

The hyundai report said "Dotarli do akcji submit 1/3" for a persona who clicked
the *link* "Zapisz się na jazdę testową" — an <a>, outside any form — three
times, before her only typing, which was a dealer-city search. The old rule
matched "zapisz" in the semantic id. Across all 49 archived sessions every
"submitted" verdict was of this kind — search boxes, search-suggestion buttons,
store-finder links — and every one flips under HTML semantics.
"""
import json

import pytest

from simulated_web_agent.main.form_analysis import _locate, classify_click, run_form_analysis
from simulated_web_agent.main.trace_store import TraceStore


def _el(html, sid="x"):
    return classify_click(_locate(html, sid))


@pytest.mark.parametrize("html,verdict", [
    ('<form><input name="email"><button parser-semantic-id="x">Wyślij</button></form>', "submit"),
    ('<form><button type="submit" parser-semantic-id="x">OK</button></form>', "submit"),
    ('<input type="submit" parser-semantic-id="x" value="Zamów">', "submit"),
    ('<a parser-semantic-id="x">Zapisz się na jazdę testową</a>', None),
    ('<input type="text" parser-semantic-id="x" placeholder="Wyszukaj produkt">', None),
    ('<button type="button" parser-semantic-id="x">Pokaż hasło</button>', None),
    ('<button parser-semantic-id="x">Dalej</button>', None),
    ('<form role="search"><input type="search"><button parser-semantic-id="x">Szukaj</button></form>',
     "search"),
])
def test_submission_follows_html_semantics(html, verdict):
    assert _el(html) == verdict


def _session(tmp_path, actions, pages):
    store = TraceStore(tmp_path)
    store.write_action_trace([json.dumps(a) for a in actions])
    for step, html in pages.items():
        store.write_simp_html(step, html)
    store.write_form_funnel = lambda *_: None
    return store


def test_the_hyundai_case_is_not_a_submission(tmp_path):
    link = '<a parser-semantic-id="zapisz_si_na_jazd_te">Zapisz się na jazdę testową</a>'
    city = '<input type="text" parser-semantic-id="wpisz_miasto" placeholder="Wpisz miasto">'
    store = _session(tmp_path,
                     [{"action": "click", "target": "zapisz_si_na_jazd_te"},
                      {"action": "type", "target": "wpisz_miasto", "text": "Kraków"}],
                     {0: link, 1: city})
    assert run_form_analysis(store)["submitted_form"] is False


def test_a_click_before_any_typing_submits_nothing(tmp_path):
    form = '<form><input name="q"><button parser-semantic-id="send">Wyślij</button></form>'
    store = _session(tmp_path,
                     [{"action": "click", "target": "send"},
                      {"action": "type", "target": "q", "text": "abc"}],
                     {0: form, 1: form})
    assert run_form_analysis(store)["submitted_form"] is False


def test_a_real_submission_is_counted(tmp_path):
    form = '<form><input name="q"><button parser-semantic-id="send">Wyślij</button></form>'
    store = _session(tmp_path,
                     [{"action": "type", "target": "q", "text": "abc"},
                      {"action": "click", "target": "send"}],
                     {0: form, 1: form})
    result = run_form_analysis(store)
    assert result["submitted_form"] is True and result["submit_clicks"] == 1


def test_a_search_is_counted_apart_from_a_conversion(tmp_path):
    form = ('<form role="search"><input type="search" name="q">'
            '<button parser-semantic-id="go">Szukaj</button></form>')
    store = _session(tmp_path,
                     [{"action": "type", "target": "q", "text": "krem"},
                      {"action": "click", "target": "go"}],
                     {0: form, 1: form})
    result = run_form_analysis(store)
    assert result["submitted_form"] is False and result["search_submits"] == 1


def test_the_report_explains_the_rule_and_the_design_limit():
    from simulated_web_agent.main.reporter_html import _render_form_funnel

    html = _render_form_funnel({"total_sessions": 3, "sessions_engaged_form": 1,
                                "sessions_submitted_form": 0})
    assert "Dotarli do akcji submit" not in html
    assert "nigdy nie wpisuje danych osobowych" in html
