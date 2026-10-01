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
    reason = _gate_reason(f)
    # Stuck, not progressing: the reason says the click was repeated to no effect
    # and that the trace cannot tell a dead element from a missed click. It does
    # not use the progress wording ("nie awaria narzędzia").
    assert "powtórzono wielokrotnie bez skutku" in reason
    assert "czy narzędzie go nie trafia" in reason
    assert "nie awaria narzędzia" not in reason


def test_a_step_that_also_failed_keeps_the_tool_failure_wording(tmp_path):
    s = _store(tmp_path, ["Slide 1", "Slide 2", "Slide 3", "Slide 4"],
               errors=[None, None, "Error executing action: Locator.click: Timeout 5000ms exceeded"])
    f = _flag(s, step=1)
    assert excluded_from_report(f) and not f["repeated_with_progress"]
    assert "narzędzie zgłosiło błąd wykonania akcji" in _gate_reason(f)


# --- a fact the page shows is not held for a click pattern near it ----------------------
#
# Joanna's "silnik … 'null kW (null KM)'" was visible in the step's own screenshot
# and was held as "needs a human" only because "Następny krok" had been clicked three
# times in a row nearby (each click advanced the wizard). In the run before, the same
# defect found by a persona whose clicks were not repeated counted as confirmed.

def _claim(store, evidence, step=1, recommendation="Naprawić."):
    f = {"heuristic_id": 2, "severity": "poważny", "step": step, "evidence": evidence,
         "recommendation": recommendation}
    return attribution.flag_harness_suspects(store, [f])[0]


PAGES = ["Silnik A", "Silnik: null kW (null KM)", "Silnik C", "Silnik D"]


def test_a_visible_fact_quoted_from_the_step_is_confirmed_not_held(tmp_path):
    f = _claim(_store(tmp_path, PAGES),
               "Parametry silnika wyświetlają się jako 'null kW (null KM)' — błąd pobierania danych.")
    assert not excluded_from_report(f)
    assert f["harness_suspect"] is False and f["requires_human_check"] is False
    assert f["repeated_with_progress"] is None and f["harness_cause"] is None
    assert f["page_fact_quotes"] == ["null kW (null KM)"]


def test_the_same_quote_on_another_step_stays_held(tmp_path):
    f = _claim(_store(tmp_path, PAGES),
               "Parametry silnika wyświetlają się jako 'null kW (null KM)'.", step=2)
    assert excluded_from_report(f) and f["page_fact_quotes"] is None


def test_a_claim_about_what_a_click_did_stays_held(tmp_path):
    pages = ["Slide 1 of 7 Mirage Green", "Slide 2 of 7 Mirage Green", "Slide 3 of 7", "Slide 4 of 7"]
    f = _claim(_store(tmp_path, pages),
               "Po kliknięciu wskaźnik 'Slide 2 of 7' się zmienił, ale nazwa 'Mirage Green' nie.")
    assert excluded_from_report(f) and f["page_fact_quotes"] is None


def test_a_claim_naming_the_repeated_control_stays_held(tmp_path):
    f = _claim(_store(tmp_path, PAGES), "Przycisk 'Next' prowadzi na stronę 'Silnik: null kW'.")
    assert excluded_from_report(f) and f["page_fact_quotes"] is None


def test_a_name_that_is_only_an_attribute_is_not_a_visible_fact(tmp_path):
    s = TraceStore(tmp_path)
    for i, body in enumerate(PAGES):
        extra = '<span aria-label="Pasek zadań"></span>' if i == 1 else ""
        s.write_simp_html(i, f'<div>{body}{extra}<button parser-semantic-id="next"><span>Next</span></button></div>')
    s.write_action_trace([json.dumps({"action": "click", "target": "next"})] * 3)
    for _ in PAGES:
        s.append_observation({"tabs": [{"url": "https://x.pl/#/e"}], "error": None})
    f = _claim(s, "Przycisk ma etykietę 'Pasek zadań', co brzmi nienaturalnie.")
    assert excluded_from_report(f) and f["page_fact_quotes"] is None


def test_a_stuck_run_is_not_released_by_a_quote(tmp_path):
    """Release is for runs that changed the page; a run that did nothing stays held."""
    f = _claim(_store(tmp_path, ["Silnik: null kW (null KM)"] * 4),
               "Parametry silnika wyświetlają się jako 'null kW (null KM)'.")
    assert excluded_from_report(f) and f["page_fact_quotes"] is None


def test_re_flagging_clears_the_release(tmp_path):
    """The stamp is idempotent: a finding re-flagged after its evidence changed is held again."""
    s = _store(tmp_path, PAGES)
    f = _claim(s, "Parametry silnika wyświetlają się jako 'null kW (null KM)'.")
    assert f["page_fact_quotes"]
    f["evidence"] = "Parametry silnika wyświetlają się jako 'brak danych o mocy'."
    attribution.flag_harness_suspects(s, [f])
    assert f["page_fact_quotes"] is None and excluded_from_report(f)

