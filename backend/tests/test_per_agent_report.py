"""
Tests for generate_per_agent_report() in reporter.py.

Behavior under test:
  3. ux_report.json is created in trace_dir with the correct top-level structure
  4. ux_report.md contains a severity header for each heuristic finding
"""
import json
import pytest
from pathlib import Path

from tests.conftest import make_finding
from src.simulated_web_agent.main.reporter import generate_per_agent_report
from src.simulated_web_agent.main.trace_store import TraceStore


def _write_trace_fixtures(store: TraceStore, findings: list[dict] | None = None) -> None:
    """Populate store with the data generate_per_agent_report reads."""
    fi = {
        "overall_score": 3.8,
        "clarity_summary": "Strona jest czytelna.",
        "scores": {
            "purpose_clarity": 4,
            "navigation_visibility": 3,
            "call_to_action": 4,
            "visual_hierarchy": 4,
            "trustworthiness": 4,
        },
        "strengths": ["Dobry nagłówek"],
        "weaknesses": ["Słaby kontrast CTA"],
    }
    store.write_first_impression(fi)
    store.write_heuristic_findings(findings or [])
    store.write_accessibility_findings({"overall_score": 4, "summary": "OK", "findings": [], "font_size_issues": []})
    store.write_viewport_findings([])
    store.write_survey_result({})
    store.write_basic_info({"persona": "Test persona", "intent": "Buy shoes"})


# ---------------------------------------------------------------------------
# Slice 3: ux_report.json structure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_agent_report_creates_json_with_required_keys(store: TraceStore):
    _write_trace_fixtures(store)

    report = await generate_per_agent_report(store)

    saved = store.read_ux_report()
    assert saved is not None, "ux_report.json must be saved"
    for key in ("first_impression", "heuristic_findings", "severity_counts", "accessibility", "viewport_findings"):
        assert key in saved, f"Expected key '{key}' in ux_report.json"


# ---------------------------------------------------------------------------
# Slice 4: ux_report.md contains severity headers for each finding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_agent_report_md_contains_severity_header_for_each_finding(store: TraceStore):
    findings = [
        make_finding(heuristic_id=1, severity="krytyczny"),
        make_finding(heuristic_id=5, severity="drobny"),
    ]
    _write_trace_fixtures(store, findings=findings)

    await generate_per_agent_report(store)

    md = (store.trace_dir / "ux_report.md").read_text(encoding="utf-8")
    assert "KRYTYCZNY" in md, "Markdown must label critical findings"
    assert "DROBNY" in md, "Markdown must label minor findings"
    assert "H1" in md, "Markdown must reference heuristic 1"
    assert "H5" in md, "Markdown must reference heuristic 5"
