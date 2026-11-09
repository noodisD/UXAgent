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
