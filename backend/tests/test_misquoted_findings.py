"""A finding that quotes words no page showed is about our transcription.

The persona reads a model's transcription of each page. In the 2026-09-25
hyundai replay that transcription rendered the Polish menu in Spanish, and the
report carried a verified serious finding, "Serwis wyświetla treści w języku
hiszpańskim", quoting "Todos SUV Eléctrico Híbrido…" — on no page of the
session. On the archive the rule holds back 3 of 34 findings with quotes, all
of them this kind, and none of the three real near misses pinned below.
"""
import pytest

from simulated_web_agent.main.attribution import flag_harness_suspects
from simulated_web_agent.main.reporter import _gate_reason, _human_check
from simulated_web_agent.main.trace_store import TraceStore

PAGE = """<html><body>
<nav><a>Modele</a><a>Wszystkie</a><a>Hybrydowe</a><a>Znajdź dealera</a></nav>
<button title="Zamknij"></button><button aria-label="Pasek zadań"></button>
<div><span>Twój Hyundai TUCSON</span><span>MODERN PLUS MY27</span></div>
<p>Brak wyników</p>
</body></html>"""

OBSERVATION = ("Modele Todos SUV Eléctrico Híbrido Encuentra un concesionario. "
               "Przycisk Zamknij. Twój Hyundai TUCSONMODERN PLUS MY27. No results.")


@pytest.fixture
def store(tmp_path):
    s = TraceStore(tmp_path)
    s.write_simp_html(1, PAGE)
    s.write_raw_html(1, PAGE)
    s.write_observation_step_text(1, OBSERVATION)
    s.write_action_trace(['{"action": "click", "target": "modele"}'] * 2)
    return s


def _flag(store, evidence):
    f = {"heuristic_id": 2, "severity": "poważny", "step": 1, "evidence": evidence}
    return flag_harness_suspects(store, [f])[0]


def test_the_spanish_menu_is_held_back(store):
    f = _flag(store, 'Treść strony w języku hiszpańskim ("Todos SUV Eléctrico Híbrido..."), '
                     'mimo że adres zawiera "/pl/pl".')
    assert f["harness_suspect"] is True
    assert f["quoted_not_on_page"] == ["Todos SUV Eléctrico Híbrido"], \
        "ellipsis stripped, the URL path is not page text"


def test_a_translated_message_is_held_back(store):
    f = _flag(store, "Po Enter obserwacja pokazuje komunikat 'No results'.")
    assert f["quoted_not_on_page"] == ["No results"]


@pytest.mark.parametrize("evidence", [
    "Przycisk 'Zamknij' nie ma widocznej etykiety.",          # a title attribute
    "Ikona 'Pasek zadań' bez tekstu.",                        # an aria-label
    "Podsumowanie pokazuje tylko 'Twój Hyundai TUCSONMODERN PLUS MY27'.",  # joined elements
    "Menu 'Znajdź dealera' jest ukryte.",                     # plain page text
])
def test_quotes_the_site_did_show_are_not(store, evidence):
    f = _flag(store, evidence)
    assert f["quoted_not_on_page"] == [] and f["harness_suspect"] is False


def test_the_findings_own_wording_is_not_blamed_on_the_transcription(store):
    """A quote on no page AND in no observation is the auditor's paraphrase —
    this rule cannot say whose fault that is, so it says nothing."""
    f = _flag(store, "Użytkownik szuka przycisku 'Umów jazdę testową'.")
    assert f["quoted_not_on_page"] == [] and f["harness_suspect"] is False


def test_the_report_says_what_to_look_for(store):
    f = _flag(store, "Komunikat 'No results' po wyszukiwaniu.")
    assert "automatycznego opisu strony" in _gate_reason(f)
    assert "„No results”" in _human_check(f)
