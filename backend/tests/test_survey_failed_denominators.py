"""A survey the tool failed to produce is not a survey that was included.

A real run: one persona's survey was {"error": "...429 RESOURCE_EXHAUSTED..."}
(the model call was rate-limited), yet the report said surveys_included = 3 while
only two surveys held any answer — and nothing said which survey was missing.
The denominator was participants minus short sessions, not participants who
answered.
"""
import pytest

from simulated_web_agent.main.reporter import _survey_failure, aggregate_statistics

ERROR = "429 RESOURCE_EXHAUSTED: quota exceeded"


def _answers(value="4"):
    return {"answers": [{"question_id": "q1", "value": value, "reason": "Czytelnie."}]}


def _report(name, survey, steps=12, ended="terminated"):
    r = {"trace_dir": f"runs/r1/{name}", "persona": f"Persona: {name}.",
         "session_result": {"steps_taken": steps, "ended_reason": ended}}
    if survey is not None:  # None: no survey_result key at all, as in older runs
        r["survey_result"] = survey
    return r


def _survey(reports):
    return aggregate_statistics(reports)["survey"]


def test_a_failed_survey_is_listed_and_not_counted():
    survey = _survey([_report("ola", _answers("4")), _report("bartek", {"error": ERROR}),
                      _report("celina", _answers("2"))])
    assert survey["surveys_included"] == 2
    assert [f["persona_dir"] for f in survey["failed_surveys"]] == ["bartek"]
    failed = survey["failed_surveys"][0]
    assert failed["persona"] == "bartek"
    assert "usterka narzędzia" in failed["reason"] and "nie wliczona" in failed["reason"]
    assert failed["error"] == ERROR
    assert survey["excluded_surveys"] == []


def test_the_failed_survey_stays_out_of_every_question_figure():
    survey = _survey([_report("ola", _answers("4")), _report("bartek", {"error": ERROR}),
                      _report("celina", _answers("2"))])
    q1 = next(q for q in survey["questions"] if q["id"] == "q1")
    assert (q1["n"], q1["avg"]) == (2, 3.0)
    assert survey["avg_scores"] == {"q1": 3.0}


def test_the_error_text_is_redacted_and_clipped():
    error = "429 RESOURCE_EXHAUSTED while handling last_action_error " + "quota exceeded " * 30
    survey = _survey([_report("ola", {"error": error})])
    shown = survey["failed_surveys"][0]["error"]
    assert "last_action_error" not in shown and "błąd interakcji" in shown
    assert shown.startswith("429 RESOURCE_EXHAUSTED")
    assert len(shown) <= 161, "~160 characters plus the ellipsis"


@pytest.mark.parametrize("failure", [
    {"error": ERROR},
    {"error": ""},
    {"raw": "Przepraszam, nie mogę odpowiedzieć."},
    {"answers": []},
    {"answers": "brak"},
    {"answers": [{"value": "4"}, "junk"]},
])
def test_every_shape_of_failure_is_caught(failure):
    survey = _survey([_report("ola", failure), _report("bartek", _answers())])
    assert [f["persona_dir"] for f in survey["failed_surveys"]] == ["ola"]
    assert survey["surveys_included"] == 1
    assert survey["failed_surveys"][0]["error"]


def test_a_participant_with_no_survey_is_neither_failed_nor_included():
    """{} and a missing survey_result are older runs and fixtures: 'no survey'."""
    survey = _survey([_report("ola", _answers()), _report("old", {}), _report("older", None)])
    assert survey["failed_surveys"] == []
    assert survey["excluded_surveys"] == []
    assert survey["surveys_included"] == 1


def test_all_surveys_failed_includes_none():
    survey = _survey([_report("ola", {"error": ERROR}), _report("bartek", {"error": ERROR})])
    assert survey["surveys_included"] == 0
    assert len(survey["failed_surveys"]) == 2
    assert survey["questions"] == []


def test_a_truncated_session_with_an_error_survey_is_listed_once_as_excluded():
    survey = _survey([_report("ola", _answers()),
                      _report("bartek", {"error": ERROR}, steps=2),
                      _report("celina", {"error": ERROR}, steps=20, ended="error")])
    assert [e["persona_dir"] for e in survey["excluded_surveys"]] == ["bartek", "celina"]
    assert survey["failed_surveys"] == []
    assert survey["surveys_included"] == 1


def test_a_survey_with_answers_is_not_failed_by_a_stray_error_key():
    survey = _survey([_report("ola", {**_answers(), "error": "late warning"})])
    assert survey["failed_surveys"] == []
    assert survey["surveys_included"] == 1


def test_a_malformed_entry_among_good_answers_is_skipped_not_fatal():
    answers = {"answers": [_answers("5")["answers"][0], "junk", {"value": "1"}]}
    survey = _survey([_report("ola", answers)])
    assert survey["failed_surveys"] == []
    assert survey["avg_scores"] == {"q1": 5.0}


@pytest.mark.parametrize("survey,failed", [
    (None, False), ({}, False), ("text", False), ([], False),
    (_answers(), False),
    # JSON the model returned with no answer in it is a failed survey, whatever
    # keys it carries; only {} / absent (older runs, fixtures) is "no survey".
    ({"questionnaire_id": "x"}, True),
    ({"error": "boom"}, True), ({"raw": "x"}, True), ({"answers": []}, True),
])
def test_survey_failure_classification(survey, failed):
    assert bool(_survey_failure(survey)) is failed


def test_a_survey_whose_answers_carry_no_value_is_a_failure():
    """An answers list that names questions but holds nothing is not a survey."""
    blank = {"answers": [{"question_id": "q1", "value": "", "reason": "?"},
                         {"question_id": "q2", "value": None}]}
    assert _survey_failure(blank)
    survey = _survey([_report("ola", _answers("4")), _report("bartek", blank)])
    assert survey["surveys_included"] == 1
    assert [f["persona_dir"] for f in survey["failed_surveys"]] == ["bartek"]


def test_a_result_under_an_unexpected_key_is_a_failure_not_silence():
    """{"responses": [...]} used to be neither failed nor included: nobody was told."""
    odd = {"responses": [{"question_id": "q1", "value": "4"}]}
    assert _survey_failure(odd)
    survey = _survey([_report("ola", _answers("4")), _report("bartek", odd)])
    assert survey["surveys_included"] == 1
    assert [f["persona_dir"] for f in survey["failed_surveys"]] == ["bartek"]


def test_an_answer_of_zero_is_an_answer():
    assert _survey_failure(_answers("0")) is None


def test_a_question_answered_twice_counts_once():
    """n and the average must not count a participant twice for one question."""
    twice = {"answers": [{"question_id": "q1", "value": "5", "reason": "a"},
                         {"question_id": "q1", "value": "1", "reason": "b"}]}
    survey = _survey([_report("ola", twice), _report("celina", _answers("3"))])
    q1 = next(q for q in survey["questions"] if q["id"] == "q1")
    assert (q1["n"], q1["avg"]) == (2, 4.0), "first answer wins: (5 + 3) / 2"
