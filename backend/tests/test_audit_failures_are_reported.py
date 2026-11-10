"""A failed audit pass is reported, never read as "no problems".

The heuristic pass returned [] when the model's reply was never usable, the
same [] as a clean audit: the persona's report said "Znaleziono 0 potwierdzonych
problemów" and a run with one such persona lost its findings silently.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main import auditor
from simulated_web_agent.main.reporter import _audit_failures
from simulated_web_agent.main.reporter_html import generate_html_report
from simulated_web_agent.main.trace_store import TraceStore


def _store(path):
    s = TraceStore(path)
    s.write_memory_trace([{"kind": "reflection", "content": "myśl"}])
    s.write_action_trace([json.dumps({"action": "click", "target": "t", "description": "klik"})])
    s.append_observation({"tabs": [{"url": "https://x.pl/"}]})
    return s


@pytest.mark.asyncio
async def test_the_failure_is_recorded_beside_the_empty_list(tmp_path):
    store = _store(tmp_path)
    with patch.object(auditor, "async_chat", new=AsyncMock(return_value='{"not": "a list"}')):
        await auditor.run_audit_analysis(store)
    assert store.read_heuristic_findings() == []
    assert store.read_audit_status()["heuristic_evaluation"]


def test_the_report_names_whose_analysis_failed(tmp_path):
    ok, bad = TraceStore(tmp_path / "a"), TraceStore(tmp_path / "b")
    ok.write_audit_status({"heuristic_evaluation": None, "first_impression": None, "accessibility": None})
    bad.write_audit_status({"heuristic_evaluation": "unusable reply", "first_impression": None,
                            "accessibility": "no_evidence"})
    failures = _audit_failures([ok, bad])
    assert [f["passes"] for f in failures] == [["ocena heurystyczna"]], "no_evidence is not a failure"
    html = generate_html_report({"website": "x.pl", "narrative": {},
                                 "statistics": {"participant_count": 2, "heuristic": {},
                                                "audit_failures": failures}})
    assert "Nie wszystkie analizy się powiodły" in html and "ocena heurystyczna" in html


def test_older_sessions_still_show_recorded_errors(tmp_path):
    s = TraceStore(tmp_path)
    s.write_first_impression({"error": "screenshot_missing"})
    assert _audit_failures([s])[0]["passes"] == ["test pierwszego wrażenia"]


# --- every analysis pass, by stable id -----------------------------------------

_ALL_AUDIT = ["heuristic_evaluation", "first_impression", "accessibility"]


def _clean(path):
    s = TraceStore(path)
    s.write_audit_status({k: None for k in _ALL_AUDIT})
    return s


def test_a_failed_survey_is_listed_with_a_stable_pass_id(tmp_path):
    s = _clean(tmp_path)
    s.write_survey_result({"error": "429 RESOURCE_EXHAUSTED"})
    (row,) = _audit_failures([s])
    assert row["pass_ids"] == ["survey"] and row["passes"] == ["ankieta"]


def test_a_crashed_audit_fails_all_three_audit_passes(tmp_path):
    """It raised before writing anything, audit_status.json included — which is
    how the crash looked like an audit with nothing to say."""
    s = TraceStore(tmp_path)
    s.write_analysis_status({"audit": "RuntimeError: boom", "survey": None})
    (row,) = _audit_failures([s])
    assert row["pass_ids"] == _ALL_AUDIT
    assert row["passes"] == ["ocena heurystyczna", "test pierwszego wrażenia", "analiza dostępności"]


def test_no_evidence_is_still_not_a_failure_and_the_ids_are_stable(tmp_path):
    bad = TraceStore(tmp_path / "b")
    bad.write_audit_status({"heuristic_evaluation": "unusable reply", "first_impression": None,
                            "accessibility": "no_evidence"})
    (row,) = _audit_failures([bad])
    assert row["pass_ids"] == ["heuristic_evaluation"]


def test_scans_that_recorded_only_errors_are_failed_passes(tmp_path):
    s = _clean(tmp_path)
    s.write_viewport_findings([{"url": "https://x.pl/", "viewport": "mobile", "error": "timeout"}])
    s.write_link_findings([{"url": "https://x.pl/", "error": "net::ERR_FAILED"}])
    (row,) = _audit_failures([s])
    assert row["pass_ids"] == ["viewport_scan", "link_check"]


def test_a_scan_that_worked_on_some_pages_is_not_a_failed_pass(tmp_path):
    s = _clean(tmp_path)
    s.write_viewport_findings([{"url": "https://x.pl/", "error": "timeout"},
                               {"url": "https://x.pl/a", "error": None}])
    assert _audit_failures([s]) == []


def test_the_ledger_names_a_scan_that_raised(tmp_path):
    s = _clean(tmp_path)
    s.write_analysis_status({"viewport_scan": "Error: browser closed", "link_check": None})
    (row,) = _audit_failures([s])
    assert row["pass_ids"] == ["viewport_scan"]


def test_a_truncated_sessions_survey_is_left_out_not_failed(tmp_path):
    """aggregate_statistics leaves such a survey out of the averages on purpose,
    and names it there; listing it as a failure too would say it twice."""
    s = _clean(tmp_path)
    s.write_survey_result({"error": "429"})
    s.write_ux_report({"persona": "Persona: Ala", "session_result": {"steps_taken": 2}})
    assert _audit_failures([s]) == []


def test_a_healthy_session_has_no_row(tmp_path):
    s = _clean(tmp_path)
    s.write_survey_result({"answers": [{"question_id": "g1", "value": 4}]})
    s.write_analysis_status({"survey": None, "audit": None, "viewport_scan": None,
                             "link_check": None})
    assert _audit_failures([s]) == []


def test_the_header_names_the_survey_and_scan_passes_too(tmp_path):
    s = _clean(tmp_path)
    s.write_survey_result({"error": "429"})
    failures = _audit_failures([s])
    html = generate_html_report({"website": "x.pl", "narrative": {},
                                 "statistics": {"participant_count": 1, "heuristic": {},
                                                "audit_failures": failures}})
    assert "Nie wszystkie analizy się powiodły" in html and "ankieta" in html


# --- a repaired pass is not a failed one ---------------------------------------------
#
# reaudit_run, resurvey_report and reanalyze_report re-run one pass and then rebuild
# the report; none of them touches analysis_status.json. The artifact is what they
# fix, so a ledger entry counts only while no artifact contradicts it.

GOOD_SURVEY = {"answers": [{"question_id": "g1", "value": 4}]}
GOOD_SCAN = [{"url": "https://x.pl/", "viewport": "mobile", "error": None, "horizontal_overflow": False}]


def test_a_survey_repaired_after_it_failed_is_not_a_failed_survey(tmp_path):
    s = _clean(tmp_path)
    s.write_analysis_status({"survey": "429 RESOURCE_EXHAUSTED"})
    assert [r["pass_ids"] for r in _audit_failures([s])] == [["survey"]], "nothing written yet: it stands"
    s.write_survey_result({"error": "429"})
    assert [r["pass_ids"] for r in _audit_failures([s])] == [["survey"]], "still an error"
    s.write_survey_result(GOOD_SURVEY)
    assert _audit_failures([s]) == []


def test_an_audit_repaired_after_it_crashed_is_not_a_failed_audit(tmp_path):
    s = TraceStore(tmp_path)
    s.write_analysis_status({"audit": "RuntimeError: boom"})
    assert _audit_failures([s])[0]["pass_ids"] == _ALL_AUDIT
    s.write_audit_status({k: None for k in _ALL_AUDIT})
    assert _audit_failures([s]) == []


def test_a_repaired_audit_that_failed_again_is_reported_by_its_new_status(tmp_path):
    s = TraceStore(tmp_path)
    s.write_analysis_status({"audit": "RuntimeError: boom"})
    s.write_audit_status({"heuristic_evaluation": None, "first_impression": None,
                          "accessibility": "429 RESOURCE_EXHAUSTED"})
    assert _audit_failures([s])[0]["pass_ids"] == ["accessibility"], "not all three, as the stale entry said"


def test_a_scan_repaired_after_it_failed_is_not_a_failed_scan(tmp_path):
    s = _clean(tmp_path)
    s.write_analysis_status({"viewport_scan": "all_pages_failed", "link_check": "Error: browser closed"})
    assert _audit_failures([s])[0]["pass_ids"] == ["viewport_scan", "link_check"]
    s.write_viewport_findings(GOOD_SCAN)
    s.write_link_findings([{"url": "https://x.pl/", "links_checked": 3, "broken": []}])
    assert _audit_failures([s]) == []


def test_a_stale_ledger_does_not_put_a_failure_into_the_coverage_sentences(tmp_path):
    """The review's probe: accessibility scored for 3 of 3 after the repair, yet the
    report said "analiza nie powiodła się dla 1"."""
    from simulated_web_agent.main.reporter import guard_unmeasured
    s = _clean(tmp_path)
    s.write_analysis_status({"audit": "RuntimeError: boom"})
    stats = {"participant_count": 3, "audit_failures": _audit_failures([s]),
             "accessibility": {"avg_score": 3.0, "scored": 3, "of": 3}}
    narr = guard_unmeasured({"accessibility_assessment": "Dostępność jest średnia."}, stats, None)
    assert narr["accessibility_assessment"] == "Dostępność jest średnia."


# --- the failures are those of the participants the report counts -------------------

@pytest.mark.asyncio
async def test_a_persona_with_no_report_is_not_listed_among_the_failures(tmp_path):
    """participant_count counts the stores that produced a ux_report; a failure of one
    that did not would make "k of n" count past n."""
    from simulated_web_agent.main.reporter import generate_aggregated_report
    from tests.conftest import make_agent_report
    stores = []
    for name in ("a", "b", "c"):
        s = _clean(tmp_path / name)
        s.write_survey_result(GOOD_SURVEY)
        if name != "c":
            report = make_agent_report()
            report["trace_dir"] = str(s.trace_dir)
            s.write_ux_report(report)
        stores.append(s)
    stores[2].write_audit_status({"heuristic_evaluation": None, "first_impression": None,
                                  "accessibility": "429"})
    stores[1].write_audit_status({"heuristic_evaluation": None, "first_impression": None,
                                  "accessibility": "429"})
    chat = AsyncMock(return_value=json.dumps({"executive_summary": "Podsumowanie."}))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        agg = await generate_aggregated_report(stores, "zadanie", "https://x.pl/")
    stats = agg["statistics"]
    assert stats["participant_count"] == 2
    assert [r["persona_dir"] for r in stats["audit_failures"]] == ["b"]


# --- what each kind of failure cost, in the header note -----------------------------

def _text(*rows):
    from simulated_web_agent.main.reporter_html import audit_failures_text
    return audit_failures_text({"audit_failures": list(rows)})


def _row(pass_ids, passes, persona="Ala"):
    return {"persona": persona, "persona_dir": persona.lower(), "pass_ids": pass_ids, "passes": passes}


def test_a_failed_audit_pass_says_its_findings_could_not_reach_the_report():
    text = _text(_row(["accessibility"], ["analiza dostępności"]))
    assert text.startswith("Nie wszystkie analizy się powiodły — Ala: analiza dostępności.")
    assert "nie mogły trafić do raportu" in text and "ankiecie" not in text and "skan" not in text.lower()
    assert text.endswith("Brak problemów nie jest tu wynikiem.")


def test_a_failed_survey_has_its_own_sentence_and_not_the_audit_one():
    text = _text(_row(["survey"], ["ankieta"]))
    assert "Odpowiedzi tych osób w ankiecie nie weszły do wyników." in text
    assert "nie mogły trafić do raportu" not in text


def test_a_failed_scan_has_its_own_sentence_and_not_the_audit_one():
    text = _text(_row(["viewport_scan", "link_check"], ["skan responsywności", "test linków"]))
    assert "część stron nie została sprawdzona" in text
    assert "nie mogły trafić do raportu" not in text and "ankiecie" not in text


def test_mixed_failures_get_each_kinds_sentence_once():
    text = _text(_row(["accessibility", "survey"], ["analiza dostępności", "ankieta"]),
                 _row(["survey", "link_check"], ["ankieta", "test linków"], persona="Ola"))
    assert text.count("nie mogły trafić do raportu") == 1
    assert text.count("Odpowiedzi tych osób w ankiecie") == 1
    assert text.count("część stron nie została sprawdzona") == 1


def test_a_row_stored_without_pass_ids_is_read_as_an_audit_failure():
    text = _text({"persona": "Ala", "passes": ["ocena heurystyczna"]})
    assert "nie mogły trafić do raportu" in text
