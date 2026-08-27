"""Cap self-reported finding confidence at what the trace actually supports.

The auditor LLM is asked how confident it is in a finding it just wrote, and it
believes itself: across the stored runs 56 of 59 findings claimed "high", 3 said
"medium", and "low" was never used — while the prompt's own example handed it the
word "high" to copy. A field that reads "high" on 95% of findings tells a client
nothing, and it is rendered as a chip on every one of them.

The trace already answers what the model is guessing at:
  • resolve_evidence (evidence.py) verifies the cited step against the trace and
    stamps `verified`;
  • flag_harness_suspects (attribution.py) stamps `harness_suspect` — "the
    interaction may have failed for reasons other than the site", which is
    word-for-word the prompt's own definition of "low".

So evidence sets the ceiling and the model may only lower it. Volunteered doubt
is kept (a model saying "medium" is arguing against its own bias, which is
signal); an unsupported "high" is not.
"""
import logging

log = logging.getLogger(__name__)

_RANK = {"low": 0, "medium": 1, "high": 2}
_BY_RANK = {v: k for k, v in _RANK.items()}


def _ceiling(finding: dict) -> tuple[str, str | None]:
    """The highest confidence this finding's evidence can support, and why."""
    if finding.get("harness_suspect"):
        return "low", "harness_suspect"
    if finding.get("off_site"):
        # The cited page is not the site under test (site_scope gate).
        return "low", "off_site"
    if finding.get("claim_verdict") == "unsupported":
        # The claim-verifier re-read the cited step's full observation and it
        # does not show the claimed problem.
        return "low", "claim_unsupported"
    if not finding.get("verified", True):
        return "low", "unverified"
    if finding.get("claim_verdict") == "partial":
        return "medium", "claim_partial"
    return "high", None


def cap_confidence(findings: list) -> list:
    """Stamp each finding's `confidence` with min(claimed, evidence ceiling).

    When the cap bites, record `confidence_capped_by` so the dashboard chip can
    explain itself. Returns the same list (findings mutated in place); non-dicts
    pass through untouched. Idempotent — safe to re-run over stored findings.
    """
    for f in findings or []:
        if not isinstance(f, dict):
            continue

        ceiling, reason = _ceiling(f)
        claimed = f.get("confidence")
        # An unparseable claim is not evidence of anything; fall back to the
        # ceiling rather than trusting or inventing a level.
        claimed_rank = _RANK.get(claimed if isinstance(claimed, str) else "",
                                 _RANK[ceiling])

        final_rank = min(claimed_rank, _RANK[ceiling])
        f["confidence"] = _BY_RANK[final_rank]

        if reason is not None and _RANK.get(claimed, -1) > _RANK[ceiling]:
            f["confidence_capped_by"] = reason
        else:
            f.pop("confidence_capped_by", None)

    return findings
