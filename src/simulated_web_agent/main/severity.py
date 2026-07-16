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


def severity_score(finding: dict) -> int:
    """Numeric 0–4 severity for a finding. Harness-suspect / needs-verification
    findings collapse to 0 (false positive) regardless of their LLM label."""
    if not isinstance(finding, dict):
        return 1
    if finding.get("harness_suspect") or finding.get("requires_human_check"):
        return 0
    return _LABEL_TO_SCORE.get(finding.get("severity"), 1)
