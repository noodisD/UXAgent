"""Confidence must be capped by checkable evidence, not self-reported.

The auditor LLM rates its confidence in findings it just wrote, and it believes
itself: across the stored runs 56 of 59 findings said "high", 3 said "medium",
and "low" was never used once — while the prompt's own example handed it the
word "high" to copy. Meanwhile the trace already answers the question the model
is guessing at: resolve_evidence stamps `verified`, and attribution stamps
`harness_suspect` (literally the rubric's definition of "low").

So the model may lower its confidence, never raise it above what the evidence
supports.
"""
from simulated_web_agent.main.confidence import cap_confidence


def _f(**kw):
    base = {"heuristic_id": 1, "severity": "poważny", "evidence": "e",
            "verified": True, "harness_suspect": False}
    return {**base, **kw}


def test_harness_suspect_finding_cannot_claim_high():
    """The interaction may have failed for reasons other than the site — that
    is exactly what "low" is for, and the model never says it."""
    findings = cap_confidence([_f(harness_suspect=True, confidence="high")])
    assert findings[0]["confidence"] == "low"


def test_unverified_citation_cannot_claim_high():
    findings = cap_confidence([_f(verified=False, confidence="high")])
    assert findings[0]["confidence"] == "low"


def test_verified_grounded_finding_keeps_high():
    findings = cap_confidence([_f(confidence="high")])
    assert findings[0]["confidence"] == "high"


def test_volunteered_doubt_is_respected():
    """A model claiming "medium" is going against its own leniency bias — that
    is signal, so cap must not round it back up."""
    findings = cap_confidence([_f(confidence="medium")])
    assert findings[0]["confidence"] == "medium"


def test_missing_claim_falls_back_to_what_evidence_supports():
    assert cap_confidence([_f()])[0]["confidence"] == "high"
    assert cap_confidence([_f(verified=False)])[0]["confidence"] == "low"


def test_garbage_claim_is_ignored_not_trusted():
    findings = cap_confidence([_f(confidence="VERY HIGH!!")])
    assert findings[0]["confidence"] == "high"
    findings = cap_confidence([_f(verified=False, confidence="whatever")])
    assert findings[0]["confidence"] == "low"


def test_non_dicts_pass_through():
    assert cap_confidence(["junk", None]) == ["junk", None]


def test_reasoning_is_recorded_for_the_reader():
    """A capped finding must say why, or the dashboard chip is unexplainable."""
    f = cap_confidence([_f(harness_suspect=True, confidence="high")])[0]
    assert f["confidence_capped_by"] == "harness_suspect"
    assert cap_confidence([_f(verified=False, confidence="high")])[0][
        "confidence_capped_by"] == "unverified"
    assert "confidence_capped_by" not in cap_confidence([_f(confidence="high")])[0]
