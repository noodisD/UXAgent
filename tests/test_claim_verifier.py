"""The truth gate: every surviving finding is checked against the evidence.

Ground truth that forced this pass: the diabetyk24.pl team verified the
5-persona report and found ~2 of 8 issues real. The disproven ones were
verifiable from the run's own trace — the "login redirect" critical cited a
step whose observation shows a filter panel and no login form; "no cart icon"
cited a step whose observation *contains* the cart's aria-label. `verified` in
evidence.py only checks the step exists; nothing compared claim to page. This
pass does exactly that, on the FULL observation (not the 400-char truncation),
and demotes what the evidence does not support.

Fail-open by contract: a verifier error must never delete findings — an
unverified report is better than a silently emptied one.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main.claim_verifier import run_claim_verification
from simulated_web_agent.main.confidence import cap_confidence
from simulated_web_agent.main.severity import excluded_from_report
from simulated_web_agent.main.trace_store import TraceStore

LONG_OBS = (
    "The page shows the search results for 'krem'. On the left there is a "
    "'Kategoria' filter sidebar with checkboxes. The product grid lists 24 "
    "items with prices. There is a header with the logo, a search bar, and "
    "a cart link with aria-label 'Koszyk, liczba produktów: 1'. "
) * 4  # ~800 chars — deliberately longer than evidence.py's 400-char quote


def _store(tmp_path):
    store = TraceStore(tmp_path / "persona")
    store.write_action_trace([
        json.dumps({"action": "click", "target": "produkt", "description": "open product"}),
        json.dumps({"action": "click", "target": "koszyk", "description": "open cart"}),
        json.dumps({"action": "scroll", "target": None, "description": "scroll"}),
    ])
    for i in range(3):
        store.append_observation({"tabs": [{"url": "https://dm.pl/x", "is_active": True}]})
        store.write_observation_step_text(i, LONG_OBS)
    return store


def _f(**kw):
    base = {"heuristic_id": 3, "severity": "krytyczny", "step": 1,
            "url": "https://dm.pl/x", "evidence": "Klik przenosi do logowania.",
            "confidence": "high", "verified": True,
            "harness_suspect": False, "off_site": False}
    return {**base, **kw}


def _reply(*verdicts):
    return json.dumps({"verdicts": [
        {"id": i, "verdict": v, "reason": f"powód {i}"} for i, v in enumerate(verdicts)
    ]})


async def test_unsupported_is_demoted_and_excluded(tmp_path):
    findings = [_f()]
    mock = AsyncMock(return_value=_reply("unsupported"))
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_store(tmp_path), findings)
    f = findings[0]
    assert f["claim_verdict"] == "unsupported"
    assert f["verdict_reason"] == "powód 0"
    assert excluded_from_report(f) is True
    cap_confidence(findings)
    assert f["confidence"] == "low"
    assert f["confidence_capped_by"] == "claim_unsupported"


async def test_partial_caps_confidence_to_medium(tmp_path):
    findings = [_f()]
    mock = AsyncMock(return_value=_reply("partial"))
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_store(tmp_path), findings)
    assert findings[0]["claim_verdict"] == "partial"
    assert excluded_from_report(findings[0]) is False
    cap_confidence(findings)
    assert findings[0]["confidence"] == "medium"


async def test_supported_is_untouched(tmp_path):
    findings = [_f()]
    mock = AsyncMock(return_value=_reply("supported"))
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_store(tmp_path), findings)
    assert findings[0]["claim_verdict"] == "supported"
    assert excluded_from_report(findings[0]) is False
    cap_confidence(findings)
    assert findings[0]["confidence"] == "high"


async def test_already_excluded_findings_are_not_sent(tmp_path):
    """harness_suspect / off_site / unverified findings are already demoted —
    don't pay LLM cost re-judging them, and don't let a stale verdict linger."""
    findings = [
        _f(harness_suspect=True, claim_verdict="supported"),   # stale verdict
        _f(off_site=True),
        _f(verified=False),
        _f(),  # the only one to verify
    ]
    mock = AsyncMock(return_value=_reply("supported"))
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_store(tmp_path), findings)
    sent = mock.await_args.args[0][0]["content"]
    sent_text = sent if isinstance(sent, str) else json.dumps(sent, ensure_ascii=False)
    # exactly one finding in the payload ("heuristic_id" appears only there,
    # never in the prompt template)
    assert sent_text.count('"heuristic_id"') == 1
    assert "claim_verdict" not in findings[0]  # stale verdict cleared
    assert findings[3]["claim_verdict"] == "supported"


async def test_verifier_sees_the_full_observation_not_the_truncation(tmp_path):
    findings = [_f()]
    mock = AsyncMock(return_value=_reply("supported"))
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_store(tmp_path), findings)
    sent = mock.await_args.args[0][0]["content"]
    sent_text = sent if isinstance(sent, str) else json.dumps(sent, ensure_ascii=False)
    # The cart aria-label sits ~500 chars in — beyond the 400-char quote, so
    # its presence proves the verifier got the full text.
    assert "Koszyk, liczba produkt" in sent_text


async def test_malformed_reply_fails_open(tmp_path):
    findings = [_f()]
    mock = AsyncMock(return_value="not json at all {{{")
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        out = await run_claim_verification(_store(tmp_path), findings)
    assert out is findings
    assert "claim_verdict" not in findings[0]
    cap_confidence(findings)
    assert findings[0]["confidence"] == "high"  # untouched


async def test_llm_error_fails_open(tmp_path):
    findings = [_f()]
    mock = AsyncMock(side_effect=RuntimeError("503"))
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_store(tmp_path), findings)
    assert "claim_verdict" not in findings[0]


async def test_unknown_verdict_value_is_ignored(tmp_path):
    """A creative verdict string must not exclude a finding."""
    findings = [_f()]
    mock = AsyncMock(return_value=json.dumps(
        {"verdicts": [{"id": 0, "verdict": "MAYBE?!", "reason": "x"}]}))
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_store(tmp_path), findings)
    assert "claim_verdict" not in findings[0]
    assert excluded_from_report(findings[0]) is False


async def test_no_verifiable_findings_makes_no_call(tmp_path):
    findings = [_f(harness_suspect=True)]
    mock = AsyncMock()
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_store(tmp_path), findings)
    assert mock.await_count == 0
