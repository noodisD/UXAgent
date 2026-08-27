"""session_result.json records how each browser session ended. Truncated
sessions (fewer than MIN_VALID_SESSION_STEPS steps) must be excluded from the
aggregated survey averages — but their first-impression scores stay valid."""

from simulated_web_agent.main.reporter import (
    MIN_VALID_SESSION_STEPS,
    _is_truncated_session,
    aggregate_statistics,
)
from simulated_web_agent.main.trace_store import TraceStore


def test_trace_store_session_result_roundtrip(tmp_path):
    store = TraceStore(tmp_path / "persona_x")
    data = {
        "steps_taken": 2,
        "max_steps": 20,
        "terminated": True,
        "ended_reason": "terminated",
    }
    store.write_session_result(data)
    assert store.read_session_result() == data


def test_read_session_result_missing_returns_none(tmp_path):
    store = TraceStore(tmp_path / "persona_y")
    assert store.read_session_result() is None


def _fake_report(persona_dir, steps_taken, survey_value, fi_score):
    return {
        "trace_dir": f"runs/run_1/{persona_dir}",
        "persona": f"Persona: {persona_dir}.",
        "first_impression": {"overall_score": fi_score, "scores": {}},
        "heuristic_findings": [],
        "accessibility": {"overall_score": 4},
        "viewport_findings": [],
        "survey_result": {
            "answers": [{"question_id": "sus1", "value": str(survey_value)}]
        },
        "session_result": {
            "steps_taken": steps_taken,
            "max_steps": 20,
            "terminated": True,
            "ended_reason": "terminated",
        },
    }


def test_is_truncated_session_threshold():
    assert _is_truncated_session(_fake_report("a", MIN_VALID_SESSION_STEPS - 1, 3, 3))
    assert not _is_truncated_session(_fake_report("b", MIN_VALID_SESSION_STEPS, 3, 3))
    # missing session_result (older runs) → never treated as truncated
    assert not _is_truncated_session({"survey_result": {}})


def test_aggregate_excludes_truncated_survey_but_keeps_first_impression():
    reports = [
        _fake_report("full_1", 18, survey_value=4, fi_score=4),
        _fake_report("full_2", 20, survey_value=2, fi_score=2),
        _fake_report("aborted", 2, survey_value=5, fi_score=3),
    ]
    stats = aggregate_statistics(reports)

    # Survey average from the two full sessions only: (4 + 2) / 2 = 3.0.
    assert stats["survey"]["avg_scores"]["sus1"] == 3.0
    assert stats["survey"]["surveys_included"] == 2
    excluded = stats["survey"]["excluded_surveys"]
    assert len(excluded) == 1
    assert excluded[0]["persona_dir"] == "aborted"
    assert excluded[0]["steps_taken"] == 2

    # First impression keeps all three sessions: (4 + 2 + 3) / 3 = 3.0.
    assert stats["first_impression"]["avg_score"] == 3.0
    assert stats["participant_count"] == 3
