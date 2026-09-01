"""
Tests for generate_aggregated_report() in reporter.py.

Behavior under test:
  - reads ux_report.json from each store via store.read_ux_report()
  - calls LLM for narrative synthesis
  - saves aggregated_report.json + .md into the runs/ parent directory
  - returns {} when no per-agent reports are available
"""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.conftest import make_agent_report, make_finding
from src.simulated_web_agent.main.reporter import generate_aggregated_report
from src.simulated_web_agent.main.trace_store import TraceStore


def _store_with_report(runs_dir: Path, idx: int, findings=None) -> TraceStore:
    """Create a TraceStore that already has a ux_report.json written."""
    store = TraceStore(runs_dir / f"run_{idx}")
    store.write_ux_report(make_agent_report(findings=findings or []), "# md")
    return store


_NARRATIVE = {
    "executive_summary": "Strona wymaga poprawy nawigacji.",
    "overall_ux_score": 3,
    "key_strengths": ["Czytelny nagłówek"],
    "critical_issues": [],
    "serious_issues": [],
    "minor_issues": [],
    "priority_recommendations": [],
    "first_impression_assessment": "OK",
    "accessibility_assessment": "OK",
    "responsiveness_assessment": "OK",
    "methodology_note": "Test",
}


@pytest.mark.asyncio
async def test_aggregated_report_saved_to_runs_dir(tmp_path: Path):
    """aggregated_report.json and .md must be written to the parent runs/ directory."""
    stores = [
        _store_with_report(tmp_path, 0, findings=[make_finding(severity="krytyczny")]),
        _store_with_report(tmp_path, 1, findings=[make_finding(severity="poważny")]),
    ]
    mock_chat = AsyncMock(return_value=json.dumps(_NARRATIVE))

    with patch("src.simulated_web_agent.main.reporter.async_chat", new=mock_chat):
        result = await generate_aggregated_report(
            stores=stores,
            task_description="Kup buty",
            website_url="https://example.com",
        )

    runs_dir = stores[0].trace_dir.parent
    assert (runs_dir / "aggregated_report.json").exists()
    assert (runs_dir / "aggregated_report.md").exists()

    assert result["statistics"]["participant_count"] == 2
    assert result["task"] == "Kup buty"
    assert result["website"] == "https://example.com"


@pytest.mark.asyncio
async def test_aggregated_report_statistics_reflect_all_agents(tmp_path: Path):
    """Statistics must aggregate findings from all per-agent reports."""
    stores = [
        _store_with_report(tmp_path, 0, findings=[
            make_finding(heuristic_id=1, severity="krytyczny"),
        ]),
        _store_with_report(tmp_path, 1, findings=[
            make_finding(heuristic_id=1, severity="krytyczny"),
            make_finding(heuristic_id=3, severity="drobny"),
        ]),
    ]
    mock_chat = AsyncMock(return_value=json.dumps(_NARRATIVE))

    with patch("src.simulated_web_agent.main.reporter.async_chat", new=mock_chat):
        result = await generate_aggregated_report(
            stores=stores,
            task_description="Test",
            website_url="https://example.com",
        )

    counts = result["statistics"]["heuristic"]["severity_counts"]
    assert counts["krytyczny"] == 2
    assert counts["drobny"] == 1

    by_h = result["statistics"]["heuristic"]["by_heuristic"]
    h1 = next(h for h in by_h if h["heuristic_id"] == 1)
    assert h1["frequency"] == 2


@pytest.mark.asyncio
async def test_aggregated_report_returns_empty_when_no_reports(tmp_path: Path):
    """generate_aggregated_report must return {} when stores have no ux_report.json."""
    stores = [TraceStore(tmp_path / "empty_run")]  # no ux_report written

    result = await generate_aggregated_report(
        stores=stores,
        task_description="Test",
        website_url="https://example.com",
    )

    assert result == {}
