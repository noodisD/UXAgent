"""NN/g severity scale 0–4 (§4.4), derived from the existing Polish labels.

  4 = usability catastrophe (blocks a critical task / stop-ship)
  3 = major
  2 = minor
  1 = cosmetic
  0 = not a real problem / false positive

Kept additive: the Polish label (krytyczny/poważny/drobny) stays the primary
field for backward compatibility; this adds a numeric score for prioritization
and maps harness-suspect findings to 0 (the doc's "błędna detekcja").
"""

_LABEL_TO_SCORE = {
    "krytyczny": 4,
    "poważny": 3,
    "drobny": 2,
}

# The Polish label is the stored one; English is what the dashboard's types use.
# This map lived twice (reporter.SEVERITY_EN and results_api._SEV_EN).
SEVERITY_EN = {"krytyczny": "critical", "poważny": "serious", "drobny": "minor"}


def severity_rank(finding_or_label) -> int:
    """Worst-first ordering rank for a severity label. Higher = worse.

    One direction, one home. There were three incompatible scales for the same
    three labels — reporter's ascending 0/1/2 (worst = min), prioritization's
    descending 1/2/3 (worst = max) and this module's NN/g 0-4 — and
    prioritization.prioritize used two of them in adjacent lines. Adding a
    fourth label meant finding all three.

    Unlike severity_score, this ignores the exclusion gates: it orders labels,
    it does not judge findings.
    """
    label = (finding_or_label.get("severity") if isinstance(finding_or_label, dict)
             else finding_or_label)
    return _LABEL_TO_SCORE.get(label, 1)


def heuristic_id(finding: dict) -> int:
    """A finding's Nielsen heuristic number, always an int.

    The value comes from an LLM, so it arrives as an int, as the string "3", or
    as an explicit null. `.get("heuristic_id", 0)` does not defend against the
    last two: null and "3" both end up as dict keys, and the resulting mixed
    str/int/None key set raises TypeError the moment anything sorts or mins it.
    That crashed aggregate_statistics, which run.py caught and then still
    finalised as "done" — a run with no report showing as complete, score 0.0.
    """
    raw = finding.get("heuristic_id")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 0
    return n if 1 <= n <= 10 else 0


def excluded_from_report(finding: dict) -> bool:
    """Should this finding stay out of headline counts, aggregate grouping and
    the prioritization matrix?

    One predicate for every consumer, so the three gates cannot drift apart:
      - harness_suspect  — interaction failure, not a site defect (attribution)
      - off_site         — the cited page is not the site under test (doz.pl /
                           gemini.pl findings shipped as diabetyk24 criticals)
      - claim_verdict == "unsupported" — the claim-verifier re-read the cited
                           step's full observation and it does not show the
                           claimed problem (ground truth: ~6 of 8 report issues
                           were this class)
    Excluded findings are still rendered, separately, as needing verification.
    """
    if not isinstance(finding, dict):
        return False
    return bool(
        finding.get("harness_suspect")
        or finding.get("off_site")
        or finding.get("claim_verdict") == "unsupported"
    )


def severity_score(finding: dict) -> int:
    """Numeric 0–4 severity for a finding. Excluded (harness-suspect, off-site,
    claim-unsupported) or needs-human-check findings collapse to 0 (false
    positive) regardless of their LLM label."""
    if not isinstance(finding, dict):
        return 1
    if excluded_from_report(finding) or finding.get("requires_human_check"):
        return 0
    return _LABEL_TO_SCORE.get(finding.get("severity"), 1)
