"""A choice that never registered cannot have been "ignored".

The hyundai report's headline critical finding: "system ignoruje wybór
użytkownika dotyczący wersji wyposażenia". EXECUTIVE was clicked six times
across all three sessions and none registered — SMART stayed ticked in the
screenshot after each one — so the summary showed SMART, correctly. Whether a
person's click would work is a real, open question, which is why the finding
goes to a verification list with the exact check, not into the bin.

Measured over 49 sessions / 172 findings: 4 flagged, 3 of them already excluded
for other reasons. The only finding whose status changes is this one.
"""
import json

from simulated_web_agent.main import attribution
from simulated_web_agent.main.reporter import _control_label, _gate_reason, _human_check
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.trace_store import TraceStore

EXECUTIVE = {
    "heuristic_id": 1, "severity": "krytyczny", "step": 12, "claim_verdict": "supported",
    "evidence": "Użytkownik świadomie wybrał wersję wyposażenia 'EXECUTIVE MY27' (akcja w "
                "kroku 4), jednak system zignorował ten wybór i pokazał SMART MY27.",
}


def _store(tmp_path, clicks):
    store = TraceStore(tmp_path)
    store.write_action_trace([json.dumps({"action": "click", "target": t}) for t in clicks])
    return store


def test_the_hyundai_claim_is_held_for_a_human(tmp_path):
    store = _store(tmp_path, ["wersja_wyposa_enia", "executive_my27_od_14", "executive_my27_od_14"])
    findings = [dict(EXECUTIVE)]
    attribution.flag_harness_suspects(store, findings)
    f = findings[0]
    assert f["harness_suspect"] is True and f["requires_human_check"] is True
    assert f["unresponsive_control"] == "executive_my27_od_14"


def test_the_check_names_the_control_as_a_person_sees_it(tmp_path):
    f = dict(EXECUTIVE, unresponsive_control="executive_my27_od_14", harness_suspect=True)
    assert _control_label(f) == "EXECUTIVE MY27", "not the parser id"
    assert "Kliknij „EXECUTIVE MY27”" in _human_check(f)
    assert "rzeczywista wada strony" in _human_check(f), "excluded is not the same as false"
    assert "nie wiadomo" in _gate_reason(f)


def test_a_single_click_is_not_evidence_of_an_unresponsive_control(tmp_path):
    """One click and a complaint is an ordinary finding; only the retry says the
    click itself may not have landed."""
    store = _store(tmp_path, ["executive_my27_od_14"])
    findings = [dict(EXECUTIVE)]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["unresponsive_control"] is None


def test_a_retried_control_without_an_ignored_choice_claim_is_left_alone(tmp_path):
    store = _store(tmp_path, ["executive_my27_od_14"] * 3)
    findings = [{"heuristic_id": 4, "severity": "drobny", "step": 1,
                 "evidence": "Nazwa 'EXECUTIVE MY27' używa żargonu producenta."}]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["unresponsive_control"] is None


def test_polish_ids_mangled_by_the_parser_still_match(tmp_path):
    """"Następny krok" arrives as nast_pny_krok: the letter is dropped, so the
    prose has to be compared with its non-ASCII letters dropped too."""
    store = _store(tmp_path, ["nast_pny_krok", "nast_pny_krok"])
    findings = [{"heuristic_id": 1, "severity": "poważny", "step": 5,
                 "evidence": "Przycisk 'Następny krok' nie zarejestrował wyboru silnika."}]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["unresponsive_control"] == "nast_pny_krok"


def test_a_reflection_restating_it_is_recognised():
    held = [dict(EXECUTIVE, harness_suspect=True, unresponsive_control="executive_my27_od_14")]
    assert attribution.restates_a_gated_finding(
        ["Kluczowy błąd w konfiguratorze spowodował, że system zignorował wybór "
         "droższej wersji EXECUTIVE."], held) is True


def test_a_verdict_resting_on_a_held_claim_is_not_scored():
    """Krzysztof's "failed" was the conclusion that the configurator ignored his
    choice. Scoring it would charge the unconfirmed claim twice."""
    stats = {"prioritization": [], "heuristic": {"severity_counts": {}},
             "accessibility": {"findings": []}, "outcomes": {"participant_count": 3},
             "objective_reflections": [
                 {"persona_dir": "k", "self_reported_only": True},
                 {"persona_dir": "m", "self_reported_only": False}]}
    outcomes = {"k": "failed", "m": "partial", "g": "success"}
    res = compute_ux_score(stats, outcomes=outcomes,
                           outcome_sources={x: "declared" for x in outcomes})
    task = [d for d in res["deductions"] if d["kind"] == "task"][0]
    assert task["amount"] == round(0.75 * 0.5 / 3, 2), "only Magdalena's partial counts"
    assert any("wstrzymany" in n["reason"] for n in res["not_counted"])


def test_the_report_lists_what_was_held_back():
    from simulated_web_agent.main.reporter_html import (
        _render_heuristic_legend,
        _render_verification_list,
    )

    stats = {"heuristic": {"by_heuristic": [], "excluded_by_heuristic": {1: 1},
             "needs_verification_items": [{
                 "heuristic_id": 1, "heuristic_name": "Widoczność statusu systemu",
                 "severity_claimed": "krytyczny", "persona_name": "Krzysztof", "step": 12,
                 "evidence": EXECUTIVE["evidence"], "screenshot": None,
                 "reason": "element nie zareagował…", "check": "Kliknij „EXECUTIVE MY27”…"}]}}
    html = _render_verification_list(stats)
    assert "Do weryfikacji przez człowieka" in html and "Kliknij „EXECUTIVE MY27”" in html
    legend = _render_heuristic_legend(stats)
    assert "1 do weryfikacji" in legend
    assert "brak potwierdzonych naruszeń" in legend, "not 'nie stwierdzono' over a held finding"
