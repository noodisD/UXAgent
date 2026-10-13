"""Fixes from reviewing the 2026-10-13 re-run (run 4654) of the Hyundai audit.

* A heuristic group holding a serious defect (two participants) and a minor remark
  (a third) was charged "serious" at three participants' reach.
* The score page said four verdicts were left out because they "rest on a held
  finding" when they had been left out for describing the tool failing.
* All six participants "agreed" that the test-drive form did not carry the configured
  model - each had reached it through the menu, two after the summary-screen button
  the task named had timed out under an overlay.
"""
import json
from pathlib import Path

import pytest

from simulated_web_agent.main.prioritization import prioritize
from simulated_web_agent.main.recurring import annotate_routes, failed_click_names, theme_text
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.trace_store import TraceStore


def _f(severity, persona, hid=2):
    return {"heuristic_id": hid, "heuristic_name": f"H{hid}", "severity": severity, "confidence": "high",
            "step": 1, "_persona_dir": persona, "_persona_name": persona, "verified": True,
            "harness_suspect": False}


OUTCOMES = {p: "success" for p in "abcdef"}


# --- reach is counted at the severity charged --------------------------------------------

def test_the_row_says_how_many_met_the_worst_version():
    (row,) = prioritize([_f("poważny", "a"), _f("poważny", "b"), _f("drobny", "c")], OUTCOMES)
    assert row["personas_affected"] == 3 and row["personas_at_worst_severity"] == 2


def _score(rows):
    return compute_ux_score({"prioritization": rows, "participant_count": 6}, outcomes=OUTCOMES,
                            outcome_sources={})


def test_a_serious_defect_is_charged_at_the_reach_of_those_who_met_it():
    (row,) = prioritize([_f("poważny", "a"), _f("poważny", "b"), _f("drobny", "c")], OUTCOMES)
    (line,) = [d for d in _score([row])["deductions"] if d["kind"] == "issue"]
    # 0.5 x (0.5 + 0.5 x 2/6) = 0.33, not 0.5 x (0.5 + 0.5 x 3/6) = 0.38
    assert line["amount"] == 0.33 and "zasięg 2/6" in line["basis"]
    assert "łącznie problem w tej heurystyce zgłosiło 3" in line["basis"]


def test_many_minor_remarks_do_not_dilute_or_inflate_one_serious_report():
    rows = prioritize([_f("poważny", "a")] + [_f("drobny", p) for p in "bcdef"], OUTCOMES)
    (line,) = [d for d in _score(rows)["deductions"] if d["kind"] == "issue"]
    assert line["amount"] == 0.29 and "zasięg 1/6" in line["basis"]


def test_a_group_with_one_severity_is_charged_as_before():
    (row,) = prioritize([_f("poważny", "a"), _f("poważny", "b")], OUTCOMES)
    (line,) = [d for d in _score([row])["deductions"] if d["kind"] == "issue"]
    assert line["amount"] == 0.33 and "wśród zgłoszeń" not in line["basis"]


# --- what the score page says it left out --------------------------------------------------

def _held_reason(why):
    stats = {"participant_count": 2, "objective_reflections": [
        {"persona_dir": "k", "self_reported_only": True, "self_reported_why": why}]}
    res = compute_ux_score(stats, outcomes={"k": "partial", "m": "success"},
                           outcome_sources={"k": "declared", "m": "declared"})
    return next(n["reason"] for n in res["not_counted"] if n["label"].startswith("Wynik zadania"))


def test_an_account_of_the_tool_failing_is_not_called_a_held_finding():
    reason = _held_reason(["tool_trouble"])
    assert "usterkę narzędzia" in reason and "wniosek wstrzymany" not in reason


def test_an_account_restating_a_held_finding_says_so():
    assert "powtarza wniosek wstrzymany" in _held_reason(["restates_held"])
    both = _held_reason(["restates_held", "harness_words"])
    assert "powtarza wniosek wstrzymany" in both and "usterkę narzędzia" in both


# --- recurring themes and the route taken -----------------------------------------------------

def _session(tmp_path, name, steps):
    """steps: [(description, failed)]"""
    store = TraceStore(tmp_path / name)
    store.write_action_trace([json.dumps({"action": "click", "target": "x", "description": d})
                              for d, _ in steps])
    store.append_observation({"tabs": [], "error": None})
    for _d, failed in steps:
        store.append_observation({"tabs": [], "error": "Error executing action: Locator.click: Timeout"
                                  if failed else None})
    return store


def test_the_controls_whose_click_failed_are_named_as_the_persona_called_them(tmp_path):
    store = _session(tmp_path, "A", [
        ("Clicking 'Umów się na jazdę testową' on the summary page", True),
        ("Clicking 'Zamknij' to close the dialog", True),
        ("Clicking 'Następny krok'", False),
        ("Clicking 'Umów się na jazdę testową' again", True)])
    assert failed_click_names(store) == ["Umów się na jazdę testową", "Zamknij"]


THEME = {"theme": "Formularz jazdy testowej nie pamięta skonfigurowanego modelu (reset do i10)",
         "participants": ["Anna", "Kamil", "Ola"], "of": 6,
         "phrases": {"Anna": "Brak przeniesienia modelu do formularza jazdy próbnej",
                     "Kamil": "Formularz jazdy testowej nie zapamiętuje modelu",
                     "Ola": "Model i10 zamiast skonfigurowanego"}}


def test_a_theme_says_who_first_tried_another_route_and_failed():
    refl = [{"persona": "Anna", "failed_clicks": ["Zapisz się na jazdę testową", "Zamknij"]},
            {"persona": "Kamil", "failed_clicks": ["Zamknij"]},
            {"persona": "Ola", "failed_clicks": []}]
    (theme,) = annotate_routes([THEME], refl)
    assert "1 z 3 tych uczestników" in theme["route_note"]
    assert "„Zapisz się na jazdę testową”" in theme["route_note"] and "Zamknij" not in theme["route_note"]
    assert "Uwaga o drodze:" in theme_text(theme)


@pytest.mark.parametrize("failed", [["Zamknij"], ["hyundai motor compan"], ["Jazda"], []])
def test_a_failed_click_on_something_else_adds_no_note(failed):
    refl = [{"persona": p, "failed_clicks": failed} for p in ("Anna", "Kamil", "Ola")]
    (theme,) = annotate_routes([THEME], refl)
    assert theme["route_note"] == "" and "Uwaga o drodze" not in theme_text(theme)


def test_the_aggregate_prompt_gets_the_routes_and_the_new_rules():
    prompt = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent" / "audit_prompts"
              / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert "{participant_routes}" in prompt
    assert "NIE jego domniemaną przyczynę techniczną" in prompt
    assert "nie łącz kilku wymiarów w jedno zdanie z jedną liczbą" in prompt
    assert "nie twierdź, że strona zawodzi na drodze z zadania" in prompt
    assert "występuje na X z X stron" in prompt


def test_the_routes_reach_the_prompt_text():
    from simulated_web_agent.main.reporter import _routes_text
    text = _routes_text([{"persona": "Ala", "route": ["1: a", "2: b (nieudane)"]}, {"persona": "Bo"}])
    assert text == "Ala: 1: a | 2: b (nieudane)"
    assert _routes_text([]) == "(brak danych)"
