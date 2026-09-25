"""An accessibility pass with nothing to scan is no measurement, not 5/5.

The prompt maps "no candidates" to 5, so all 8 archived sessions that saw
nothing of the site — 4 that never loaded, 4 stuck on a Cloudflare page — were
written up as "strona jest dostępna", 5/5 in green on the dashboard. All 13
sessions that saw a real site scored 1 or 2.
"""
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main import auditor
from simulated_web_agent.main.trace_store import TraceStore


@pytest.mark.asyncio
async def test_nothing_scanned_is_no_evidence_and_asks_no_model(tmp_path):
    chat = AsyncMock()
    with patch.object(auditor, "_chat_json", new=chat):
        res = await auditor._run_accessibility_summary(TraceStore(tmp_path))
    assert res == {"error": "no_evidence"}
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_a_session_behind_a_bot_wall_is_no_evidence(tmp_path):
    store = TraceStore(tmp_path)
    store.write_a11y_snapshot(0, '- heading "Performing verification"')
    for i in range(2):
        store.write_observation_step_text(i, "Performing verification. Checking your browser.")
    store.write_action_trace(['{"action": "wait"}'] * 2)
    chat = AsyncMock()
    with patch.object(auditor, "_chat_json", new=chat):
        res = await auditor._run_accessibility_summary(store)
    assert res == {"error": "no_evidence"}
    chat.assert_not_called()
