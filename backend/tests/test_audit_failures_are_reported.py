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
