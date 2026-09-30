"""A control whose accessible name describes nothing is reported, by code.

In the 2026-09-30 hyundai run five of six participants noticed that the price-info
icon buttons are named "Pasek zadań" ("Taskbar") and a gallery button "Flyout".
Both are real aria-labels (753 and 35 instances in the recorded snapshots) that a
sighted visitor never sees, so the participants' wording ("nienaturalne
tłumaczenie") was held — and the defect itself, which is only for screen-reader
users, appeared nowhere because the scan asked only whether a name EXISTS."""
import asyncio

import pytest

from simulated_web_agent.main import auditor, attribution
from simulated_web_agent.main.a11y_checks import (
    a11y_topic, extract_generic_name_findings, generic_name_reason, generic_names_in_session,
)
from simulated_web_agent.main.reporter import _human_check
from simulated_web_agent.main.trace_store import TraceStore

PAGE_A = "https://www.hyundai.com/pl/pl/modele/nowy-tucson/konfigurator.html#/summary"
PAGE_B = "https://www.hyundai.com/pl/pl/modele/nowy-tucson/konfigurator.html#/interior"
PAGE_C = "https://www.hyundai.com/pl/pl/"

SUMMARY = """- main:
  - heading "Twój Hyundai TUCSON" [level=2]
  - button "Pasek zadań"
  - button "Pasek zadań"
  - button "Pasek zadań"
  - link "Zapytaj o ofertę"
  - button "Dalej"
"""
INTERIOR = """- main:
  - button "Flyout":
    - img
  - button "Pasek zadań"
  - link "Wnętrze"
"""
HOME = """- main:
  - link "Modele"
  - button "Menu"
"""


def _store(tmp_path, pages):
    store = TraceStore(tmp_path / "session")
    for i, (url, text) in enumerate(pages):
        store.append_observation({"tabs": [{"url": url, "is_active": True}], "error": None})
        store.write_a11y_snapshot(i, text)
    return store


@pytest.mark.parametrize("role, name, expected", [
    ("button", "Pasek zadań", "jargon"), ("button", "Flyout", "jargon"),
    ("button", "  TASKBAR ", "jargon"), ("link", "Link", "jargon"), ("button", "Przycisk", "jargon"),
    ("button", "Dalej", None), ("link", "Link do regulaminu", None), ("button", "Menu", None),
    ("checkbox", "MODERN PLUS MY27", None), ("button", "", None),
    ("img", "image", None), ("heading", "Flyout", None),   # only controls carry this rule
])
def test_the_rule_flags_names_that_describe_nothing_and_no_others(role, name, expected):
    assert generic_name_reason(role, name) == expected


def test_one_finding_with_the_worst_page_and_the_spread(tmp_path):
    store = _store(tmp_path, [(PAGE_A, SUMMARY), (PAGE_B, INTERIOR), (PAGE_C, HOME)])
    (finding,) = extract_generic_name_findings(store)
    assert finding["category"] == "Nieopisowa nazwa kontrolki" and finding["severity"] == "drobny"
    assert finding["step"] == 0, "the summary page has the most: three"
    assert "3 kontrolki mają nazwę, która nie opisuje ich działania" in finding["detail"]
    assert "„Pasek zadań” (przycisk, 3×)" in finding["detail"]
    assert "Problem występuje na 2 z 3 odwiedzonych stron" in finding["detail"]
    assert {(n["name"], n["pages"]) for n in finding["generic_names"]} == {("Pasek zadań", 2), ("Flyout", 1)}
    assert finding["deterministic"] is True


@pytest.mark.parametrize("count, phrase", [
    (1, "1 kontrolka ma nazwę, która nie opisuje jej działania"),
    (2, "2 kontrolki mają nazwę, która nie opisuje ich działania"),
    (5, "5 kontrolek ma nazwę, która nie opisuje ich działania"),
])
def test_the_sentence_agrees_with_the_count(tmp_path, count, phrase):
    text = "- main:\n" + '  - button "Pasek zadań"\n' * count
    (finding,) = extract_generic_name_findings(_store(tmp_path, [(PAGE_A, text)]))
    assert phrase in finding["detail"]


def test_a_clean_site_produces_nothing(tmp_path):
    assert extract_generic_name_findings(_store(tmp_path, [(PAGE_C, HOME)])) == []


def test_its_wording_is_its_own_topic_not_a_missing_name():
    """The aggregator groups findings by topic recomputed from the text; read as
    control_name it would merge into "Brak nazwy kontrolki" and lose its meaning."""
    text = "Nieopisowa nazwa kontrolki Na najgorszej stronie 3 kontrolki mają nazwę, która nie opisuje ich działania"
    assert a11y_topic(text) == "generic_name"
    assert a11y_topic("Brak nazwy kontrolki: przycisk bez nazwy dostępnej dla czytników") == "control_name"


# --- wired into the audit, after the model -------------------------------------

def _run(monkeypatch, store, reply):
    async def chat(*_a, **_k):
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(auditor, "_chat_json", chat)
    return asyncio.run(auditor._run_accessibility_summary(store))


def test_it_is_added_after_the_models_findings_not_instead_of_them(tmp_path, monkeypatch):
    store = _store(tmp_path, [(PAGE_A, SUMMARY), (PAGE_B, INTERIOR)])
    llm = {"overall_score": 3, "summary": "S.", "findings": [
        {"category": "Brak nazwy kontrolki", "severity": "poważny", "detail": "d", "step": 0}]}
    result = _run(monkeypatch, store, llm)
    assert [f["category"] for f in result["findings"]] == ["Brak nazwy kontrolki", "Nieopisowa nazwa kontrolki"]
    assert result["overall_score"] == 3


def test_a_failed_model_call_stays_a_failure(tmp_path, monkeypatch):
    store = _store(tmp_path, [(PAGE_A, SUMMARY)])
    result = _run(monkeypatch, store, auditor._UnusableReply("nie JSON"))
    assert "error" in result and "findings" not in result


# --- a held remark that names one points to it ---------------------------------

def test_the_session_reports_which_distinctive_names_its_pages_had(tmp_path):
    store = _store(tmp_path, [(PAGE_A, SUMMARY), (PAGE_B, INTERIOR)])
    assert generic_names_in_session(store) == {"pasek zadań", "flyout"}
    plain = _store(tmp_path / "x", [(PAGE_C, '- main:\n  - link "Link"\n')])
    assert generic_names_in_session(plain) == set(), "'link' is not distinctive enough to match text"


def test_a_held_finding_naming_it_is_pointed_at_the_accessibility_section(tmp_path):
    store = _store(tmp_path, [(PAGE_A, SUMMARY)])
    finding = {"heuristic_id": 2, "severity": "drobny", "step": 0,
               "evidence": "Przycisk ma etykietę „Pasek zadań”, co brzmi nienaturalnie.",
               "recommendation": "Zmienić etykietę."}
    other = {"heuristic_id": 2, "severity": "drobny", "step": 0,
             "evidence": "Cena jest nieczytelna.", "recommendation": "Powiększyć."}
    attribution.flag_harness_suspects(store, [finding, other])
    assert finding["named_in_accessibility"] == ["pasek zadań"]
    assert other["named_in_accessibility"] is None
    check = _human_check(finding)
    assert "w sekcji Dostępność" in check and "widzący użytkownik tej nazwy nie zobaczy" in check
    assert "pomiń" not in check.lower(), "it must not tell the reader to discard a real finding"
