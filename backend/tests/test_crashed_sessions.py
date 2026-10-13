"""A session the tool itself crashed is not evidence about the site.

2026-10-13 hyundai run: two of six sessions ended with
`TypeError: data must be str, not list` - the perceiver (a lite model) returned its
observation as a list and the step loop wrote it to disk as text. The report then
scored one of them as a failed task, quoted "krytyczny błąd techniczny" as the
participant's blocker, and printed "wyeliminować błąd powodujący zrywanie sesji w
konfiguratorze" as the most important fix - a tool crash handed to the client as a
site defect."""
import json

import pytest

from simulated_web_agent.agent.agent import observation_text
from simulated_web_agent.main.iso_metrics import effective_outcome
from simulated_web_agent.main.redaction import describes_session_crash
from simulated_web_agent.main.reporter import (
    _human_check, _is_truncated_session, aggregate_statistics,
)
from simulated_web_agent.main.reporter_html import SESSION_ERROR_NOTE, generate_html_report
from simulated_web_agent.main.trace_store import TraceStore


# --- the cause -------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("jeden tekst", "jeden tekst"),
    (["zdanie pierwsze", "zdanie drugie"], "zdanie pierwsze\nzdanie drugie"),
    ([["zagnieżdżone"], "obok"], "zagnieżdżone\nobok"),
    ({"tekst": "x"}, '{"tekst": "x"}'),
    (None, ""), ([], ""), (7, "7"),
])
def test_the_perceivers_answer_is_always_one_string(value, expected):
    assert observation_text(value) == expected


def test_a_trace_write_never_ends_a_session(tmp_path):
    store = TraceStore(tmp_path / "s")
    store.write_observation_step_text(3, ["a", "b"])
    assert json.loads((store.trace_dir / "observation_trace" / "observation_trace_3.txt").read_text("utf-8")) == ["a", "b"]


# --- what the report makes of a crashed session --------------------------------------------

def _crashed(tmp_path, name, ended_reason, *, steps=15, blockers=None, fix="Naprawić nakładki."):
    store = TraceStore(tmp_path / name)
    store.trace_dir.mkdir(parents=True, exist_ok=True)
    store.write_session_result({"steps_taken": steps, "max_steps": 60,
                                "terminated": False, "ended_reason": ended_reason})
    store.write_objective_reflection({
        "outcome": "failed", "summary": "Nie osiągnęła celu z powodu błędu technicznego.",
        "blockers": blockers if blockers is not None else [
            "Krytyczny błąd techniczny przerywający sesję użytkownika wewnątrz konfiguratora.",
            "Brak wyjaśnienia przy komunikacie „Niedostępne”."],
        "wins": ["Czytelne ceny w dolnym pasku."], "priorityFix": fix})
    report = {"trace_dir": str(store.trace_dir), "persona": f"Persona: {name}", "intent": "Cel badania.",
              "first_impression": {}, "heuristic_findings": [], "accessibility": {"findings": []},
              "survey_result": {"answers": []}, "session_result": store.read_session_result()}
    return store, report


def test_a_crash_is_an_unknown_outcome_not_a_failed_task(tmp_path):
    store, _ = _crashed(tmp_path, "A", "error")
    assert effective_outcome(store) == ("unknown", "error")
    ok, _ = _crashed(tmp_path, "B", "terminated")
    assert effective_outcome(ok)[1] != "error"


def test_a_crash_after_many_steps_still_drops_the_survey(tmp_path):
    assert _is_truncated_session({"session_result": {"ended_reason": "error", "steps_taken": 21}})
    assert not _is_truncated_session({"session_result": {"ended_reason": "terminated", "steps_taken": 21}})


def test_the_account_of_a_crash_does_not_reach_the_client_as_a_site_problem(tmp_path):
    store, report = _crashed(tmp_path, "Małgorzata", "error",
                             fix="Wyeliminować krytyczny błąd techniczny powodujący zrywanie sesji.")
    (row,) = aggregate_statistics([report], stores=[store])["objective_reflections"]
    assert row["session_error"] == {"steps": 15}
    assert row["effective_outcome"] == "unknown"
    assert row["summary"] == "" and row["priority_fix"] == ""
    # what the participant saw BEFORE the crash stands
    assert row["blockers"] == ["Brak wyjaśnienia przy komunikacie „Niedostępne”."]
    assert row["wins"] == ["Czytelne ceny w dolnym pasku."]


def test_a_normal_session_keeps_its_account(tmp_path):
    store, report = _crashed(tmp_path, "Ola", "terminated")
    (row,) = aggregate_statistics([report], stores=[store])["objective_reflections"]
    assert "session_error" not in row
    assert row["summary"] and row["priority_fix"] == "Naprawić nakładki."
    assert len(row["blockers"]) == 2


def test_the_crashed_task_is_not_charged_to_the_score(tmp_path):
    from simulated_web_agent.main.scoring import _task_line
    crashed, _ = _crashed(tmp_path / "e", "A", "error")
    normal, _ = _crashed(tmp_path / "n", "A", "terminated")
    def line(store):
        outcome, source = effective_outcome(store)
        return _task_line({"a": outcome}, {"a": source})
    assert line(crashed) is None
    # the model's "failed" on a session that ended normally IS charged
    assert line(normal) is not None


@pytest.mark.parametrize("text, expected", [
    ("Krytyczny błąd techniczny przerywający sesję użytkownika wewnątrz konfiguratora.", True),
    ("Wyeliminowanie krytycznego błędu technicznego powodującego zrywanie sesji w konfiguratorze", True),
    ("Błąd techniczny (crash sesji) uniemożliwiający znalezienie dealera", True),
    ("Persona nie osiągnęła celu, bo sesja została przedwcześnie przerwana", True),
    # remarks about the site stay
    ("Brak wyjaśnienia przy komunikacie „Niedostępne”.", False),
    ("Formularz nie zapamiętuje wybranego modelu.", False),
    ("Zbyt mały tekst w stopce.", False), ("", False),
])
def test_what_counts_as_blaming_the_crash(text, expected):
    assert describes_session_crash(text) is expected


# --- the document --------------------------------------------------------------------

def _agg(rows):
    return {"website": "x.pl", "task": "Wspólny cel badania.",
            "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                          "serious_issues": [], "minor_issues": []},
            "statistics": {"participant_count": len(rows), "objective_reflections": rows,
                           "heuristic": {"by_heuristic": []}}}


def _row(name, objective="Wspólny cel badania.", **extra):
    return {"persona": name, "objective": objective, "outcome": "failed", "summary": f"Opis {name}.",
            "blockers": [], "wins": ["Coś działa."], "priority_fix": "", **extra}


def test_the_html_says_the_session_was_the_tools_failure():
    html = generate_html_report(_agg([_row("A", effective_outcome="unknown", outcome_source="error",
                                           session_error={"steps": 15}, summary=""),
                                      _row("B")]))
    assert SESSION_ERROR_NOTE.format(steps=15) in html
    assert "wynik nie jest oceną serwisu" in html and "Wynik nieustalony" in html


def test_a_goal_every_participant_shares_is_printed_once():
    html = generate_html_report(_agg([_row("A"), _row("B"), _row("C")]))
    assert html.count('class="obj-task"') == 1
    different = generate_html_report(_agg([_row("A", objective="Cel 1."), _row("B", objective="Cel 2.")]))
    assert different.count('class="obj-task"') == 2


def test_the_word_file_says_the_same(tmp_path):
    from docx import Document
    from simulated_web_agent.main.report_docx import build_docx
    agg = _agg([_row("A", effective_outcome="unknown", outcome_source="error",
                     session_error={"steps": 15}, summary=""), _row("B")])
    text = " | ".join(p.text for p in Document(build_docx(agg, tmp_path, tmp_path / "r.docx")).paragraphs)
    assert SESSION_ERROR_NOTE.format(steps=15) in text
    assert text.count("Cel: Wspólny cel badania.") == 1


# --- a held finding is checked the way it was held --------------------------------------------

def test_a_claim_about_a_name_is_not_checked_by_clicking():
    held = {"repeated_with_progress": "Następny krok", "requires_human_check": True,
            "evidence": "Obraz ma angielski tekst alternatywny „Interior view of model KONA”.",
            "recommendation": "Przetłumaczyć teksty alternatywne na język polski."}
    check = _human_check(held)
    assert "Kliknij" not in check and "narzędzi deweloperskich" in check


def test_a_claim_about_what_a_click_did_is_still_checked_by_clicking():
    held = {"repeated_with_progress": "Następny krok", "requires_human_check": True,
            "evidence": "Po kliknięciu przycisk nie zmienia ceny.", "recommendation": "Naprawić."}
    assert "Kliknij „Następny krok”" in _human_check(held)


def test_the_aggregate_prompt_forbids_generalising_when_participants_disagree():
    from pathlib import Path as P
    prompt = (P(__file__).resolve().parents[1] / "src" / "simulated_web_agent" / "audit_prompts"
              / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert "sprzeczne obserwacje" in prompt and "nie uogólniaj wady" in prompt
    assert "proś o wdrożenie czegoś, co w innej ścieżce już działa" in prompt
