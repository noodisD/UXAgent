"""A session that never saw the site is not a participant.

run.py knew which sessions observed nothing — no step taken, or every step on
a bot-detection challenge — but used that only to fail a run where all of them
did. In a mixed run the dead session counted everywhere: adding one 0-step
session to the 2026-09-24 hyundai run moved its accessibility average from 1.0
to 2.0 (a 5/5 pass over nothing), its score from 4.0 to 3.8 and its task line
to "nieosiągnięty: 1/4", for a page nobody saw. hebe blocks only some
sessions, so mixed runs are the realistic case.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_agent_report, make_finding
from simulated_web_agent.main.attribution import why_site_unseen
from simulated_web_agent.main.reporter import generate_aggregated_report
from simulated_web_agent.main.reporter_html import generate_html_report
from simulated_web_agent.main.trace_store import TraceStore

_NARRATIVE = {"executive_summary": "Podsumowanie.", "overall_ux_score": 3,
              "critical_issues": [], "serious_issues": [], "minor_issues": [],
              "priority_recommendations": []}


def _store(root, name, steps, observations=()):
    s = TraceStore(root / name)
    s.write_ux_report(make_agent_report(findings=[make_finding(severity="poważny")]))
    s.write_session_result({"steps_taken": steps})
    for i, text in enumerate(observations):
        s.write_observation_step_text(i, text)
    s.write_action_trace(['{"action": "click", "target": "x"}'] * len(observations))
    return s


def test_the_reasons(tmp_path):
    assert why_site_unseen(_store(tmp_path, "a", 5, ["Strona główna"])) is None
    assert "żadnego kroku" in why_site_unseen(_store(tmp_path, "b", 0))
    wall = _store(tmp_path, "c", 2, ["Performing verification. Checking your browser"] * 2)
    assert "weryfikacji antybotowej" in why_site_unseen(wall)


@pytest.mark.asyncio
async def test_a_mixed_run_counts_only_who_saw_the_site(tmp_path):
    stores = [_store(tmp_path, "a", 5, ["Strona główna"]),
              _store(tmp_path, "b", 4, ["Konfigurator"]),
              _store(tmp_path, "dead", 0)]
    with patch("simulated_web_agent.main.reporter.async_chat",
               new=AsyncMock(return_value=json.dumps(_NARRATIVE))):
        agg = await generate_aggregated_report(stores=stores, task_description="Zadanie",
                                               website_url="https://x.pl")
    stats = agg["statistics"]
    assert stats["participant_count"] == 2
    assert [e["persona_dir"] for e in stats["excluded_sessions"]] == ["dead"]
    html = generate_html_report(agg)
    assert "Pominięto 1 z 3 sesji" in html and "żadnego kroku" in html


@pytest.mark.asyncio
async def test_a_run_nobody_saw_is_not_emptied_here(tmp_path):
    """run.py fails such a run before reporting; a direct caller keeps them all."""
    stores = [_store(tmp_path, "a", 0), _store(tmp_path, "b", 0)]
    with patch("simulated_web_agent.main.reporter.async_chat",
               new=AsyncMock(return_value=json.dumps(_NARRATIVE))):
        agg = await generate_aggregated_report(stores=stores, task_description="Z",
                                               website_url="https://x.pl")
    assert agg["statistics"]["participant_count"] == 2
    assert agg["statistics"]["excluded_sessions"] == []
