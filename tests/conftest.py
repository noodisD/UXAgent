"""Shared fixtures for UXAgent tests."""
import json
from pathlib import Path

import pytest

from src.simulated_web_agent.main.trace_store import TraceStore


def make_agent_report(
    *,
    fi_score: float = 4.0,
    fi_dimension_scores: dict | None = None,
    findings: list[dict] | None = None,
    a11y_score: int = 4,
    viewport_findings: list[dict] | None = None,
) -> dict:
    """Build a minimal per-agent ux_report dict for use in tests."""
    return {
        "persona": "Test persona",
        "intent": "Test intent",
        "first_impression": {
            "overall_score": fi_score,
            "clarity_summary": "Test summary",
            "scores": fi_dimension_scores or {
                "purpose_clarity": 4,
                "navigation_visibility": 4,
                "call_to_action": 4,
                "visual_hierarchy": 4,
                "trustworthiness": 4,
            },
            "strengths": ["Good header"],
            "weaknesses": ["Small CTA"],
        },
        "heuristic_findings": findings or [],
        "severity_counts": {
            "krytyczny": sum(1 for f in (findings or []) if f.get("severity") == "krytyczny"),
            "poważny": sum(1 for f in (findings or []) if f.get("severity") == "poważny"),
            "drobny": sum(1 for f in (findings or []) if f.get("severity") == "drobny"),
        },
        "accessibility": {
            "overall_score": a11y_score,
            "summary": "OK",
            "findings": [],
            "font_size_issues": [],
        },
        "viewport_findings": viewport_findings or [],
        "survey_result": {},
    }


def make_finding(
    heuristic_id: int = 1,
    severity: str = "poważny",
    url: str = "https://example.com",
    step: int = 1,
) -> dict:
    return {
        "heuristic_id": heuristic_id,
        "heuristic_name": f"Heuristic {heuristic_id}",
        "severity": severity,
        "url": url,
        "step": step,
        "evidence": "Test evidence",
        "recommendation": "Test recommendation",
    }


@pytest.fixture
def store(tmp_path: Path) -> TraceStore:
    """A temporary TraceStore (creates all subdirs automatically)."""
    return TraceStore(tmp_path)


@pytest.fixture
def trace_dir(store: TraceStore) -> Path:
    """Backward-compat alias — returns the store's trace_dir Path."""
    return store.trace_dir
