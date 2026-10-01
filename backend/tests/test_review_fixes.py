"""The second review round: each confirmed finding has a test that fails without its fix.

Grouped by where the finding sat: URLs, numbers, plurals, wording."""
import re

import pytest

from simulated_web_agent.main.reporter import (
    _format_root_causes, _measured_basis, _harness_reason, _human_check,
)
from simulated_web_agent.main.reporter_html import (
    _render_accessibility_detail, _render_broken_links, _render_scale_question,
    _unconfirmed_note, evidence_quote, held_intro_text,
)
from simulated_web_agent.main.scoring import breakdown_text, rationale_text
from simulated_web_agent.main.textutil import decimal_text, display_url, sessions_in, view_key


# --- URLs --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["https://[domena]/x", "https://[ścieżka]/x", "http://[::1",
                                 "https://[invalid/path", "http://ｆｏｏ／"])
def test_a_url_that_does_not_parse_does_not_stop_a_report(bad):
    assert isinstance(view_key(bad), str) and isinstance(display_url(bad), str)


def test_the_same_page_under_two_query_strings_is_listed_once():
    stats = {"accessibility": {"findings": [{
        "category": "Brak nazwy", "severity": "poważny", "verified": True, "topic": "control_name",
        "detail": "d", "recommendation": "r", "personas": ["Ala"],
        "urls": ["https://x.pl/a?p=1", "https://x.pl/a?p=2", "https://x.pl/b"]}]}}
    html = _render_accessibility_detail(stats)
    assert html.count("x.pl/a") == 1 and "x.pl/b" in html


# --- numbers -----------------------------------------------------------------------

@pytest.mark.parametrize("value, digits, expected", [(3.0, 1, "3,0"), (0.5, 2, "0,50"), (22, 1, "22,0"),
                                                     (None, 1, "—"), ("n/a", 1, "n/a"), (True, 1, "True")])
def test_one_decimal_separator(value, digits, expected):
    assert decimal_text(value, digits) == expected


def _result(n_items):
    items = [{"label": f"Pozycja {i}", "amount": 0.5, "basis": "b", "kind": "k"} for i in range(n_items)]
    return {"score": 3.0, "start": 5.0, "raw": 3.0, "floored": False, "not_counted": [],
            "total_deduction": 0.5 * n_items, "deductions": items}


def test_the_score_rationale_uses_the_reports_decimal_comma():
    text = rationale_text(_result(2))
    assert "Ocena 3,0/5: od 5,0 odjęto 1,00 pkt" in text and "−0,50" in text
    assert not re.search(r"\d\.\d", text), "no dot decimals left"
    assert "Punkt wyjścia: 5,0" in breakdown_text(_result(2)) and "−0,50" in breakdown_text(_result(2))


@pytest.mark.parametrize("n_items, tail", [
    (5, "i 1 mniejsza pozycja"), (6, "i 2 mniejsze pozycje"), (9, "i 5 mniejszych pozycji")])
def test_the_number_of_smaller_items_agrees_with_its_noun(n_items, tail):
    assert tail in rationale_text(_result(n_items))


def test_the_headline_score_and_the_breakdown_table_print_commas():
    from simulated_web_agent.main.reporter_html import generate_html_report
    agg = {"website": "x.pl", "task": "t", "narrative": {"overall_ux_score": 3.0, "executive_summary": "S.",
                                                          "critical_issues": [], "serious_issues": [],
                                                          "minor_issues": []},
           "statistics": {"participant_count": 1, "score_breakdown": _result(2)}}
    html = generate_html_report(agg)
    assert 'class="val">3,0<span' in html and 'class="val">3.0' not in html
    assert "−0,50" in html and "0.50" not in html


# --- plurals and counts in the text ---------------------------------------------------

def test_a_single_held_finding_reads_in_the_singular():
    one, two = held_intro_text(1), held_intro_text(2)
    assert "Wstrzymano wniosków: 1." in one and "go potwierdzić" in one and "nie trafił do" in one
    assert "jest fałszywy" in one and "Wymaga" in one and "ich" not in one
    assert "Wstrzymano wniosków: 2." in two and "ich potwierdzić" in two and "Każdy wymaga" in two


@pytest.mark.parametrize("taps, contrast, overflow, expected", [
    (1, 1, 1, "1 mały cel dotykowy, 1 błąd kontrastu, 1 strona z przewijaniem poziomym"),
    (2, 3, 4, "2 małe cele dotykowe, 3 błędy kontrastu, 4 strony z przewijaniem poziomym"),
    (5, 12, 22, "5 małych celów dotykowych, 12 błędów kontrastu, 22 strony z przewijaniem poziomym"),
])
def test_the_measured_line_agrees_with_each_count(taps, contrast, overflow, expected):
    stats = {"viewport": {"devices": [{"viewport": "mobile", "tap_targets_below_min": taps,
                                       "contrast_failures": contrast, "pages_with_overflow": overflow}]}}
    issue = {"heuristic_name": "Responsywność", "impact": "Małe cele dotykowe na urządzeniach mobilnych"}
    assert expected in _measured_basis(issue, stats, None, None)


def test_broken_links_agree_in_the_measured_line_the_html_and_the_session_count():
    issue = {"heuristic_name": "Uszkodzone linki", "impact": "link prowadzi donikąd"}
    one = _measured_basis(issue, {}, None, {"links": {"broken_urls": ["https://x.pl/a"]}})
    assert "1 uszkodzony link —" in one
    two = _measured_basis(issue, {}, None, {"links": {"broken_urls": ["https://x.pl/a", "https://x.pl/b"]}})
    assert "2 uszkodzone linki —" in two
    html = _render_broken_links({"total_links_checked": 9, "broken_urls": ["https://x.pl/a"],
                                 "sessions_with_broken_links": 1})
    assert "1 uszkodzony link w 1 sesji" in html
    assert "w 3 sesjach" in _render_broken_links({"total_links_checked": 9, "sessions_with_broken_links": 3,
                                                   "broken_urls": ["a", "b", "c", "d", "e"]})
    assert sessions_in(1) == "w 1 sesji" and sessions_in(2) == "w 2 sesjach"


def test_the_note_about_unconfirmed_issues_agrees_with_its_count():
    one = _unconfirmed_note({"unconfirmed_issues": [{"heuristic_id": 1, "heuristic_name": "Status"}]})
    assert "1 problem z podsumowania, dla którego" in one and "Jego źródłowe" in one
    three = _unconfirmed_note({"unconfirmed_issues": [{"heuristic_id": i, "heuristic_name": "n"} for i in (1, 2, 3)]})
    assert "3 problemy z podsumowania, dla których" in three
    five = _unconfirmed_note({"unconfirmed_issues": [{"heuristic_id": i, "heuristic_name": "n"} for i in range(5)]})
    assert "5 problemów z podsumowania" in five


def test_the_root_cause_line_is_only_about_a_defect_filed_under_several_heuristics():
    alone = {"name": "Brak raty", "heuristics_involved": [1], "personas_affected": 1, "worst_severity": "drobny"}
    assert _format_root_causes([alone]) == "(brak wykrytych powiązań między różnymi heurystykami)"
    pair = {"name": "Brak raty", "heuristics_involved": [2, 7], "personas_affected": 1, "worst_severity": "drobny"}
    text = _format_root_causes([alone, pair])
    assert "zgłoszona pod 2 różnymi heurystykami (H2, H7), zgłoszona przez 1 uczestnika" in text
    assert "1 uczestników" not in text and "niezależnie" not in text and "H1" not in text
    many = dict(pair, personas_affected=3)
    assert "potwierdzona niezależnie przez 3 uczestników" in _format_root_causes([many])


def test_a_quoted_sentence_is_cut_at_a_word_not_in_the_middle_of_one():
    long = "Brakuje prezentacji estymowanej raty miesięcznej, choć klienci opierają decyzję " * 8
    label, text = evidence_quote({"observation_quote": long}, limit=300)
    assert text.endswith("…") and len(text) <= 301
    assert not text[:-1].endswith(("d", "o")) or text[:-1].rsplit(" ", 1)[-1] in long.split()


def test_the_survey_scale_is_the_questions_own():
    html = _render_scale_question({
        "id": "synthetic_seq", "prompt": "Łatwość (SEQ)", "min": 1, "max": 7, "avg": 4.67, "n": 3,
        "low_label": "trudno", "high_label": "łatwo",
        "distribution": [{"value": v, "count": 1 if v in (4, 5, 6) else 0} for v in range(1, 8)]})
    assert "śr. 4,67 / 7" in html
    assert html.count('class="sv-tick"') == 7
    assert "left:61%" in html, "(4.67 - 1) / 6, not (4.67 - 1) / 4"


# --- wording ----------------------------------------------------------------------------

def test_the_perception_and_bot_wall_sentences_do_not_claim_more_than_the_trace_shows():
    perception = _harness_reason({"code": "perception_gap", "step": 2})
    assert "mogło nie odczytać" in perception and "zapisana strona była duża" in perception
    wall = _harness_reason({"code": "bot_wall", "step": 2})
    assert "wyglądała na ekran weryfikacji antybotowej" in wall
    check = _human_check({"harness_suspect": True, "harness_cause": {"code": "bot_wall"}})
    assert "zwykle dotyczy tylko zautomatyzowanych odwiedzin" in check
    assert "nie zwykłych użytkowników" not in check


def test_the_accessibility_pointer_says_the_scan_reports_it_if_it_found_it():
    note = _human_check({"named_in_accessibility": ["Pasek zadań"], "claim_verdict": "unsupported"})
    assert "o ile je wykrył" in note, "it does not promise an entry the scan may not have produced"


# --- the 2026-10-01 rerun: what the report still got wrong -------------------------------------

@pytest.mark.parametrize("text", [
    "Brak nazw dla przycisków. Na najgorszej stronie brakuje nazwy dla 2 przycisków.",
    "Brak nazwy pola wyboru (combobox).",
    "Zidentyfikowano 3 przyciski bez przypisanej nazwy dostępnej dla czytników ekranu.",
    "Pole jest bez przypisanej nazwy dla czytnika ekranu.",
])
def test_every_way_of_saying_a_control_has_no_name_is_one_topic(text):
    from simulated_web_agent.main.a11y_checks import a11y_topic
    assert a11y_topic(text) == "control_name"


def test_unnamed_buttons_and_an_unnamed_combobox_are_one_finding_not_two(tmp_path):
    """The 2026-10-01 report listed unnamed buttons as their own finding AND again under
    the combobox's ("Również: 3 przyciski bez nazwy"), and deducted for both."""
    from tests.test_a11y_instances import _session
    from simulated_web_agent.main.reporter import aggregate_statistics

    def finding(category, detail, url):
        return {"category": category, "severity": "poważny", "detail": detail, "url": url,
                "step": 1, "verified": True, "recommendation": "Nadać nazwę."}

    a, ra = _session(tmp_path, "a", "desktop", "Anna", finding(
        "Brak nazw dla przycisków",
        "Na najgorszej stronie (krok 23) brakuje nazwy dla 2 przycisków.", "https://x.pl/dealer"))
    b, rb = _session(tmp_path, "b", "desktop", "Basia", finding(
        "Brak nazw dla pól wyboru (combobox)",
        "Na najgorszej stronie (krok 25) brakuje nazwy dla 1 pola wyboru (combobox).", "https://x.pl/form"))
    findings = aggregate_statistics([ra, rb], stores=[a, b])["accessibility"]["findings"]
    assert len(findings) == 1 and findings[0]["topic"] == "control_name"
    assert len(findings[0]["instances"]) == 2


def test_the_narrative_does_not_carry_the_tools_field_names():
    from simulated_web_agent.main.reporter import resolve_narrative
    from simulated_web_agent.main.redaction import clean_narrative
    text = "Nie zaobserwowano wylewania się treści (mobile_overflow) ani problemów z przewijaniem (tablet_overflow)."
    assert clean_narrative(text) == "Nie zaobserwowano wylewania się treści ani problemów z przewijaniem."
    assert clean_narrative("etykieta 'open_tooltip' w przycisku") == "etykieta 'open_tooltip' w przycisku", \
        "a page's own label in quotes is evidence, not ours"
    nested = clean_narrative({"a": ["x (mobile_overflow)"], "b": {"c": "y (tap_targets_below_min)"}, "n": 3})
    assert nested == {"a": ["x"], "b": {"c": "y"}, "n": 3}
    agg = {"narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                         "serious_issues": [], "minor_issues": [],
                         "responsiveness_assessment": text}, "statistics": {}}
    assert "mobile_overflow" not in resolve_narrative(agg)["responsiveness_assessment"]


@pytest.mark.parametrize("text, expected", [
    ("Wyskakujący na początku sesji pop-up blokujący szybki dostęp do informacji ofertowych.", False),
    ("Nakładające się warstwy interfejsu (divy) blokujące klikanie w przyciski", True),
    ("Okno udostępniania blokuje przyciski konfiguratora.", True),
])
def test_a_popup_that_blocks_the_view_is_not_a_blocked_click(text, expected):
    from simulated_web_agent.main.redaction import describes_interaction_trouble
    assert describes_interaction_trouble(text) is expected


@pytest.mark.asyncio
async def test_the_survey_summary_is_given_the_mean_beside_the_answers():
    """"Ocenili pozytywnie" was written under a question whose answers averaged 3,17 of 5."""
    import json
    from unittest.mock import AsyncMock, patch

    from simulated_web_agent.main.reporter import _summarize_survey
    answers = [{"question_id": "g1", "value": str(v), "reason": "r"} for v in (2, 3, 3, 3, 4, 4)]
    answers.append({"question_id": "e1", "value": "Brak raty", "reason": ""})
    chat = AsyncMock(return_value=json.dumps({"summaries": {"g1": "x", "e1": "y"}}))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        await _summarize_survey([{"survey_result": {"answers": answers}}])
    messages = chat.call_args.args[0]
    payload = json.loads(messages[1]["content"])
    assert payload["g1"]["średnia"] == 3.17 and len(payload["g1"]["odpowiedzi"]) == 6
    assert isinstance(payload["e1"], list), "an open question has no mean"
    assert "the tone must agree with it" in messages[0]["content"]


def test_the_arithmetic_is_in_the_reports_number_format_and_plurals_agree():
    from tests.test_scoring import _issue, _stats
    from simulated_web_agent.main.scoring import compute_ux_score
    res = compute_ux_score(_stats(prioritization=[_issue(1, "poważny", 1, n=6, conf="medium")],
                                  gated=4, participants=6),
                           outcomes={"a": "success"}, outcome_sources={"a": "declared"})
    basis = res["deductions"][0]["basis"]
    assert "problem poważny (0,5) × zasięg 1/6 (0,5 + 0,5 × 0,17 = 0,58) × pewność średnia (0,75)" in basis
    assert not re.search(r"\d\.\d", basis), basis
    labels = [n["label"] for n in res["not_counted"]]
    assert "4 wnioski wykluczone przez weryfikację" in labels
    for gated, expected in ((1, "1 wniosek wykluczony"), (5, "5 wniosków wykluczonych")):
        again = compute_ux_score(_stats(gated=gated), outcomes={"a": "success"},
                                 outcome_sources={"a": "declared"})
        assert any(n["label"].startswith(expected) for n in again["not_counted"]), expected


@pytest.mark.parametrize("n, expected", [(1, "Wynik zadania 1 uczestnika"), (2, "Wynik zadania 2 uczestników"),
                                         (5, "Wynik zadania 5 uczestników")])
def test_held_participants_are_counted_in_the_right_form(n, expected):
    from tests.test_scoring import _stats
    from simulated_web_agent.main.scoring import compute_ux_score
    ids = [f"p{i}" for i in range(n)]
    stats = _stats(participants=n)
    stats["objective_reflections"] = [{"persona_dir": d, "self_reported_only": True} for d in ids]
    res = compute_ux_score(stats, outcomes={d: "failed" for d in ids},
                           outcome_sources={d: "declared" for d in ids})
    assert any(x["label"] == expected for x in res["not_counted"]), [x["label"] for x in res["not_counted"]]

