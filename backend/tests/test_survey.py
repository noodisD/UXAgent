"""
Tests for run_survey() in survey.py.

Behavior under test:
  - reads memory trace from store
  - calls LLM with questionnaire + memory content
  - writes parsed result via store.write_survey_result()
  - stores a fallback dict when LLM returns non-JSON
"""
import json
import pytest
from unittest.mock import AsyncMock, patch

from src.simulated_web_agent.main.survey import run_survey
from src.simulated_web_agent.main.trace_store import TraceStore


@pytest.mark.asyncio
async def test_survey_reads_memory_and_writes_result(store: TraceStore):
    """run_survey must read memory from store and persist the LLM result."""
    memory = [
        {"kind": "observation", "content": "Loaded homepage.", "timestamp": 1, "importance": 0.5},
        {"kind": "reflection", "content": "Navigation unclear.", "timestamp": 2, "importance": 0.7},
    ]
    store.write_memory_trace(memory)

    expected = {"q1": "tak", "q2": "nie"}
    mock_chat = AsyncMock(return_value=json.dumps(expected))

    with patch("src.simulated_web_agent.main.survey.async_chat", new=mock_chat):
        result = await run_survey(store, questionnaire={"q1": "Czy znalazłeś to, czego szukałeś?"})

    assert result == expected
    assert store.read_survey_result() == expected


@pytest.mark.asyncio
async def test_survey_includes_questionnaire_in_llm_call(store: TraceStore):
    """The questionnaire content must appear in the message sent to the LLM."""
    store.write_memory_trace([])
    questionnaire = {"navigation_clarity": "Was navigation intuitive?"}
    captured_messages = []

    async def capture(messages, **kwargs):
        captured_messages.extend(messages)
        return json.dumps({"navigation_clarity": "yes"})

    with patch("src.simulated_web_agent.main.survey.async_chat", new=AsyncMock(side_effect=capture)):
        await run_survey(store, questionnaire=questionnaire)

    user_content = " ".join(
        m["content"] for m in captured_messages if m["role"] == "user"
    )
    assert "navigation_clarity" in user_content
    assert "Was navigation intuitive?" in user_content


@pytest.mark.asyncio
async def test_survey_stores_fallback_on_invalid_json(store: TraceStore):
    """run_survey must not raise when the LLM returns non-JSON; it stores a fallback."""
    store.write_memory_trace([])
    mock_chat = AsyncMock(return_value="Przepraszam, nie mogę odpowiedzieć.")

    with patch("src.simulated_web_agent.main.survey.async_chat", new=mock_chat):
        result = await run_survey(store, questionnaire={})

    assert isinstance(result, dict)
    saved = store.read_survey_result()
    assert saved is not None
