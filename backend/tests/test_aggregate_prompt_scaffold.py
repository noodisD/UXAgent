"""The aggregate prompt's fixed text must not contradict the data beside it.

It called the completion block "MEASURED FROM THE SESSION TRACE" whether or not
it was (most configurator runs are only declared), said that block fixes
overall_ux_score (the score is computed in code), and explained survey ids with
a legend from an older questionnaire (q1=ease of finding products …) while the
current one uses g1…g9, e1, e2 and a 1-7 SEQ scale.
"""
import re
from pathlib import Path

from simulated_web_agent.main.reporter import _survey_legend

SRC = Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
PROMPT = (SRC / "audit_prompts" / "aggregate_synthesize.txt").read_text(encoding="utf-8")
REPORTER = (SRC / "main" / "reporter.py").read_text(encoding="utf-8")

QUESTIONS = {"questions": [
    {"id": "g1", "kind": "scale", "prompt": "Strona była łatwa w obsłudze.", "min": 1, "max": 5},
    {"id": "synthetic_seq", "kind": "scale", "prompt": "Łatwość (SEQ)", "min": 1, "max": 7},
    {"id": "e1", "kind": "open", "prompt": "Czego brakowało?"},
    {"id": "", "kind": "scale", "prompt": "bez id"},
]}


def test_every_placeholder_in_the_prompt_is_filled_by_the_caller():
    """A placeholder nobody fills raises KeyError, and the aggregate step is the
    last thing a run does: the whole report is lost at the end of the run."""
    wanted = set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", PROMPT))
    call = REPORTER[REPORTER.index("prompt_template.format("):]
    call = call[:call.index("\n    )\n")]
    given = set(re.findall(r"^\s+([a-z_]+)=", call, re.M))
    assert wanted == given, (sorted(wanted - given), sorted(given - wanted))


def test_the_legend_is_built_from_this_studys_questions():
    legend = _survey_legend(QUESTIONS)
    assert "- g1 = Strona była łatwa w obsłudze. (skala 1–5)" in legend
    assert "- synthetic_seq = Łatwość (SEQ) (skala 1–7)" in legend
    assert "- e1 = Czego brakowało? (pytanie otwarte)" in legend
    assert "bez id" not in legend


def test_no_questionnaire_is_not_a_crash():
    assert _survey_legend(None) == "(brak pytań ankiety)"
    assert _survey_legend({"questions": []}) == "(brak pytań ankiety)"


def test_the_prompt_no_longer_carries_the_old_hand_written_legend():
    assert "q1=ease of finding products" not in PROMPT
    assert "{survey_legend}" in PROMPT


def test_completion_is_not_called_measured_unconditionally():
    assert "MEASURED FROM THE SESSION TRACE" not in PROMPT
    assert "ZMIERZONY" in PROMPT and "DEKLAROWANY" in PROMPT
    assert "ustalaniu\noverall_ux_score" not in PROMPT


def test_the_prompt_says_how_to_use_the_site_context():
    # A function the site does not offer is a limit of its scope, not a defect to
    # repair: the pepco report's top recommendation was to fix a store locator for a
    # feature the site never had. The rule adds no placeholder (the guard above).
    assert "WAŻNE (kontekst serwisu)" in PROMPT
    assert "nie są wadami interfejsu" in PROMPT
    assert "Nie\nrekomenduj ich" in PROMPT
    assert "granica zakresu serwisu" in PROMPT
    assert "Zadanie zakłada funkcję" in PROMPT
    assert "do not assume the site is a shop" in PROMPT
