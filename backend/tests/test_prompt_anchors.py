"""Prompt examples must not hand the model an answer to copy.

This defect has now appeared three times, each time silently:
  • first_impression showed [4,3,2,4,4] -> 3.4, and 71% of 38 stored scans
    returned exactly those scores — including t-mobile.pl, a site nothing like
    the discount retailer the rest were run against;
  • heuristic_evaluate showed "confidence": "high", and 56 of 59 findings said
    "high" while "low" was never used once;
  • accessibility_summarize showed "overall_score": 3, and 62% of 35 scans
    returned exactly 3.

The rule is about *judgement* fields — a score, a rating, a confidence, a
severity: the things the model is being paid to decide. Illustrative prose and
sample data (a URL, "11px", a category name) are fine, and a *varied* set of
examples is fine too: memory_importance shows 9/3/1 as a calibration rubric and
its output is well spread across 11 distinct values.
"""
import re

import pytest

from simulated_web_agent.agent.prompt_registry import registry as _prompts

# Judgement fields, and the prompt(s) that must not pre-fill them.
_JUDGEMENT_FIELDS = (
    "overall_score", "overall_ux_score", "score", "confidence",
    "severity", "rating", "impact_score",
)

_PROMPTS_WITH_JSON_EXAMPLES = (
    "first_impression",
    "heuristic_evaluate",
    "accessibility_summarize",
    "aggregate_synthesize",
)

# "field": <placeholder> is the fix; "field": 3 / "high" is the defect.
_PREFILLED = re.compile(
    r'"(' + "|".join(_JUDGEMENT_FIELDS) + r')"\s*:\s*(?!<)("?[0-9]|"(?:high|medium|low|'
    r'krytyczny|poważny|drobny)")'
)


@pytest.mark.parametrize("name", _PROMPTS_WITH_JSON_EXAMPLES)
def test_prompt_does_not_prefill_a_judgement_field(name):
    prompt = _prompts.get(name)
    hits = [m.group(0) for m in _PREFILLED.finditer(prompt)]
    assert not hits, (
        f"{name}.txt pre-fills a judgement field in its example: {hits}. "
        "Use a <placeholder> — the model copies concrete values."
    )
