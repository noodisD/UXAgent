"""Repeated clicks that changed the page are held for a check, not blamed on us.

Three identical clicks read as a stuck loop, and a finding on those steps was
held as "prawdopodobna awaria narzędzia, nie strony" with "Powtórz tę
interakcję ręcznie i sprawdź, czy strona reaguje". For three archived findings
the page had changed on every click — the colour step's exterior-view gallery
("next", Slide 1 -> 4 of 8) and the wizard's "Następny krok". They stay held
(one was false, one debatable), with the reason that is true.
"""
import json

from simulated_web_agent.main import attribution
from simulated_web_agent.main.reporter import _gate_reason, _human_check
from simulated_web_agent.main.severity import excluded_from_report
from simulated_web_agent.main.trace_store import TraceStore


def _store(tmp_path, pages, errors=()):
    s = TraceStore(tmp_path)
    for i, body in enumerate(pages):
        s.write_simp_html(i, f'<div>{body}<button parser-semantic-id="next"><span>Next</span></button></div>')
    s.write_action_trace([json.dumps({"action": "click", "target": "next"})] * (len(pages) - 1))
    for i in range(len(pages)):
        s.append_observation({"tabs": [{"url": "https://x.pl/#/colours"}],
                              "error": errors[i] if i < len(errors) else None})
    return s


def _flag(store, step=1):
    f = {"heuristic_id": 4, "severity": "poważny", "step": step,
         "evidence": "Przycisk wprowadza w błąd.", "recommendation": "Zmienić."}
    return attribution.flag_harness_suspects(store, [f])[0]


def test_a_run_that_changed_the_page_is_progress(tmp_path):
    s = _store(tmp_path, ["Slide 1 of 8", "Slide 2 of 8", "Slide 3 of 8", "Slide 4 of 8"])
    assert attribution.progress_run_steps(s) == {0: "Next", 1: "Next", 2: "Next"}
    assert {0, 1, 2} <= attribution.harness_suspect_steps(s)
    assert not {0, 1, 2} & attribution.harness_suspect_steps(s, progress_runs=False)


def test_its_finding_stays_held_with_the_true_reason(tmp_path):
    f = _flag(_store(tmp_path, ["Slide 1", "Slide 2", "Slide 3", "Slide 4"]))
    assert excluded_from_report(f), "held exactly as before"
    assert f["repeated_with_progress"] == "Next"
    assert "nie awaria narzędzia" in _gate_reason(f)
    assert "Kliknij „Next” kilka razy" in _human_check(f)


def test_a_stuck_run_keeps_the_tool_failure_wording(tmp_path):
    f = _flag(_store(tmp_path, ["Slide 1"] * 4))
    assert excluded_from_report(f) and not f["repeated_with_progress"]
    assert "awaria narzędzia, nie strony" in _gate_reason(f)


def test_a_step_that_also_failed_keeps_the_tool_failure_wording(tmp_path):
    s = _store(tmp_path, ["Slide 1", "Slide 2", "Slide 3", "Slide 4"],
               errors=[None, None, "Error executing action: Locator.click: Timeout 5000ms exceeded"])
    f = _flag(s, step=1)
    assert excluded_from_report(f) and not f["repeated_with_progress"]
