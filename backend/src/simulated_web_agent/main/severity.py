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
