"""
Tests for run_all_analyses() in analysis_runner.py.

Behavior under test:
  - run_survey, run_audit_analysis, and run_viewport_scan are all called with the store
  - generate_per_agent_report is called after the parallel phase completes
  - the returned value is the per-agent report dict
"""
import pytest
from unittest.mock import AsyncMock, call, patch

from simulated_web_agent.main.analysis_runner import run_all_analyses
from simulated_web_agent.main.trace_store import TraceStore

_PATCH = "simulated_web_agent.main.analysis_runner.{}"


@pytest.mark.asyncio
async def test_all_three_analyses_are_called_with_store(store: TraceStore):
    """survey, audit, and viewport scan must each be called once with the store."""
    survey_mock = AsyncMock(return_value={"q1": "tak"})
    audit_mock = AsyncMock(return_value={})
    viewport_mock = AsyncMock(return_value=[])
    report_mock = AsyncMock(return_value={"heuristic_findings": []})

    # Every pass patched: the ones this test does not name (cognitive walkthrough,
    # objective reflection, bias audit) call the model, and unpatched they made real
    # calls that retried for minutes when the provider was unreachable.
    with _passes(
        run_survey=survey_mock,
        run_audit_analysis=audit_mock,
        run_viewport_scan=viewport_mock,
        generate_per_agent_report=report_mock,
    ):
        await run_all_analyses(store, questionnaire={"q1": "?"}, headless=True)

    survey_mock.assert_called_once_with(store, {"q1": "?"})
    audit_mock.assert_called_once_with(store)
    viewport_mock.assert_called_once_with(store, headless=True)


@pytest.mark.asyncio
async def test_per_agent_report_generated_after_analyses(store: TraceStore):
    """generate_per_agent_report must be called exactly once, after the parallel phase."""
    call_order: list[str] = []

    async def survey_side(*_a, **_kw):
        call_order.append("survey")

    async def audit_side(*_a, **_kw):
        call_order.append("audit")

    async def viewport_side(*_a, **_kw):
        call_order.append("viewport")

    expected_report = {"first_impression": {}, "heuristic_findings": []}

    async def report_side(*_a, **_kw):
        call_order.append("report")
        return expected_report

    with _passes(
        run_survey=AsyncMock(side_effect=survey_side),
        run_audit_analysis=AsyncMock(side_effect=audit_side),
        run_viewport_scan=AsyncMock(side_effect=viewport_side),
        generate_per_agent_report=AsyncMock(side_effect=report_side),
    ):
        result = await run_all_analyses(store, questionnaire={}, headless=True)

    # report must come after all three analyses
    assert "report" in call_order
    assert call_order.index("report") > call_order.index("survey")
    assert call_order.index("report") > call_order.index("audit")
    assert call_order.index("report") > call_order.index("viewport")
    assert result == expected_report


# --- the failure ledger: one status file, stable pass ids --------------------

import contextlib


@contextlib.contextmanager
def _passes(**overrides):
    """Every pass patched, so a test says only what it makes fail."""
    defaults = {
        "run_survey": AsyncMock(return_value={"answers": [{"question_id": "g1", "value": 4}]}),
        "run_audit_analysis": AsyncMock(return_value={}),
        "run_viewport_scan": AsyncMock(return_value=[]),
        "run_link_check": AsyncMock(return_value=[]),
        "run_cognitive_walkthrough": AsyncMock(return_value=[]),
        "generate_per_agent_report": AsyncMock(return_value={"heuristic_findings": []}),
        "generate_objective_reflection": AsyncMock(return_value={}),
        "run_bias_audit": AsyncMock(return_value={}),
    }
    defaults.update(overrides)
    with contextlib.ExitStack() as stack:
        for name, mock in defaults.items():
            stack.enter_context(patch(_PATCH.format(name), mock))
        yield defaults


@pytest.mark.asyncio
async def test_a_crashing_pass_is_recorded_and_the_other_stages_still_run(store: TraceStore):
    """A pass that raises wrote no artifact, so it looked like a pass that found
    nothing. It is named in analysis_status.json, and the report is still built."""
    with _passes(run_survey=AsyncMock(side_effect=RuntimeError("quota exhausted"))) as m:
        report = await run_all_analyses(store, questionnaire={}, headless=True)

    m["generate_per_agent_report"].assert_awaited_once()
    m["run_bias_audit"].assert_awaited_once()
    assert report == {"heuristic_findings": []}
    status = store.read_analysis_status()
    assert status["survey"] == "RuntimeError: quota exhausted"
    assert status["audit"] is None and status["viewport_scan"] is None


@pytest.mark.asyncio
async def test_a_pass_that_returned_an_error_is_recorded_too(store: TraceStore):
    page_error = {"url": "https://x.pl/", "error": "net::ERR_NAME_NOT_RESOLVED"}
    with _passes(run_survey=AsyncMock(return_value={"error": "429 RESOURCE_EXHAUSTED"}),
                 run_viewport_scan=AsyncMock(return_value=[page_error, dict(page_error)]),
                 run_link_check=AsyncMock(return_value=[page_error])):
        await run_all_analyses(store, questionnaire={}, headless=True)
    status = store.read_analysis_status()
    assert status["survey"] == "429 RESOURCE_EXHAUSTED"
    assert status["viewport_scan"] == "all_pages_failed"
    assert status["link_check"] == "all_pages_failed"


@pytest.mark.asyncio
async def test_an_unparseable_survey_reply_is_recorded(store: TraceStore):
    with _passes(run_survey=AsyncMock(return_value={"raw": "to nie jest JSON"})):
        await run_all_analyses(store, questionnaire={}, headless=True)
    assert store.read_analysis_status()["survey"] == "unparseable_reply"


@pytest.mark.asyncio
async def test_a_scan_that_worked_on_some_pages_is_not_a_failed_pass(store: TraceStore):
    """Partial failure is counted from the findings; the ledger names only a pass
    that produced nothing. [] is a run with no pages to visit, not a failure."""
    with _passes(run_viewport_scan=AsyncMock(return_value=[
                     {"url": "https://x.pl/", "error": "timeout"},
                     {"url": "https://x.pl/a", "error": None}]),
                 run_link_check=AsyncMock(return_value=[])):
        await run_all_analyses(store, questionnaire={}, headless=True)
    status = store.read_analysis_status()
    assert status["viewport_scan"] is None and status["link_check"] is None


@pytest.mark.asyncio
async def test_odd_return_values_from_the_passes_are_not_failures(store: TraceStore):
    with _passes(run_survey=AsyncMock(return_value={"q1": "tak"}),
                 run_audit_analysis=AsyncMock(return_value=None),
                 run_viewport_scan=AsyncMock(return_value={"x": 1}),
                 run_link_check=AsyncMock(return_value=None)):
        await run_all_analyses(store, questionnaire={}, headless=True)
    assert not any(store.read_analysis_status().values())


@pytest.mark.asyncio
async def test_a_crash_after_the_parallel_block_is_recorded(store: TraceStore):
    with _passes(run_bias_audit=AsyncMock(side_effect=ValueError("bad shape")),
                 generate_objective_reflection=AsyncMock(side_effect=KeyError("outcome"))):
        await run_all_analyses(store, questionnaire={}, headless=True)
    status = store.read_analysis_status()
    assert status["bias_audit"] == "ValueError: bad shape"
    assert status["objective_reflection"].startswith("KeyError")
    assert status["survey"] is None, "the parallel block's entries are kept"


@pytest.mark.asyncio
async def test_a_status_file_that_cannot_be_written_does_not_cost_the_report(store: TraceStore):
    with _passes() as m, patch.object(TraceStore, "write_analysis_status",
                                      side_effect=OSError("disk full")):
        report = await run_all_analyses(store, questionnaire={}, headless=True)
    assert report == {"heuristic_findings": []}
    m["generate_per_agent_report"].assert_awaited_once()
