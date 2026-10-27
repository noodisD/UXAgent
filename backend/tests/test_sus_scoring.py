"""SUS is scored per participant, never as an average of its ten items.

The items alternate wording: "I thought the site was easy to use" is positive,
"I found the site unnecessarily complex" negative. A run's report averaged the
raw answers across items ("2,15/5, bardzo nisko") — a number that means
nothing, because a 4 on a negative item is good news. The two valid surveys of
that run, scored properly, were Julia 80 and Filip 42.5.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main import reporter, results_api, sus
from simulated_web_agent.main.iso_metrics import _numeric_survey_avg
from simulated_web_agent.main.reporter import aggregate_statistics
from simulated_web_agent.main.survey import run_survey
from simulated_web_agent.main.sus import (
    SUS_ITEMS, polarity, questions_from_manifest, sus_items, sus_score,
)

JULIA = [3, 1, 4, 1, 4, 1, 4, 1, 2, 1]
FILIP = [1, 4, 1, 1, 2, 1, 4, 5, 1, 1]


def _questions(markers=False):
    return [
        {"id": f"sus{i}", "type": "multiple_choice", "prompt": p,
         "options": ["1", "2", "3", "4", "5"], **({"sus_item": i} if markers else {})}
        for i, p in enumerate(SUS_ITEMS, start=1)
    ]


ITEMS = {f"sus{i}": i for i in range(1, 11)}


def _by_qid(values):
    return {f"sus{i}": v for i, v in enumerate(values, start=1)}


def _report(name, values, steps=12, reason="Tak to wyglądało."):
    return {
        "trace_dir": f"runs/r1/{name}",
        "persona": f"Persona: {name}\nWiek: 30",
        "survey_result": {"answers": [
            {"question_id": f"sus{i}", "value": str(v), "reason": reason}
            for i, v in enumerate(values, start=1)]},
        "session_result": {"steps_taken": steps, "ended_reason": "terminated"},
    }


def _sus(reports, questions=None):
    return aggregate_statistics(reports, q_meta=questions or _questions())["survey"]["sus"]


# --- the arithmetic ----------------------------------------------------------

def test_best_possible_answers_score_100():
    # positives all 5, negatives all 1
    assert sus_score(_by_qid([5, 1] * 5), ITEMS) == (100.0, None)


def test_worst_possible_answers_score_0():
    assert sus_score(_by_qid([1, 5] * 5), ITEMS) == (0.0, None)


def test_all_threes_score_50():
    assert sus_score(_by_qid([3] * 10), ITEMS) == (50.0, None)


@pytest.mark.parametrize("values,expected", [(JULIA, 80.0), (FILIP, 42.5)])
def test_the_two_archived_answer_sets(values, expected):
    assert sus_score(_by_qid(values), ITEMS) == (expected, None)


def test_a_raw_item_average_would_have_been_wrong():
    """Pins why the score exists: Julia's raw mean says 'low', her SUS says 80."""
    raw_mean = sum(JULIA) / len(JULIA)
    assert raw_mean < 2.5 and sus_score(_by_qid(JULIA), ITEMS)[0] == 80.0


@pytest.mark.parametrize("mutate,reason", [
    (lambda a: a.pop("sus7"), "incomplete: 9 of 10"),
    (lambda a: a.update(sus7="cannot assess"), "incomplete: 9 of 10"),
    (lambda a: a.update(sus7=None), "incomplete: 9 of 10"),
    (lambda a: a.clear(), "incomplete: 0 of 10"),
    (lambda a: a.update(sus7=9), "out of range"),
    (lambda a: a.update(sus7=0), "out of range"),
])
def test_no_score_without_ten_valid_answers(mutate, reason):
    answers = _by_qid(JULIA)
    mutate(answers)
    assert sus_score(answers, ITEMS) == (None, reason)


# --- is this questionnaire SUS? ---------------------------------------------

def test_detected_by_the_canonical_wording():
    assert sus_items(_questions()) == ITEMS


def test_wording_match_ignores_case_spacing_and_the_final_stop():
    qs = _questions()
    qs[0]["prompt"] = "  i THINK I would like   to use this site frequently  "
    qs[1]["prompt"] = qs[1]["prompt"].rstrip(".")
    assert sus_items(qs) == ITEMS


def test_detected_by_the_marker_whatever_the_wording():
    qs = _questions(markers=True)
    for q in qs:
        q["prompt"] = "Pozycja SUS po polsku: " + q["id"]
    assert sus_items(qs) == ITEMS


def test_one_edited_prompt_means_it_is_not_sus():
    qs = _questions()
    qs[3]["prompt"] = "I think I would need a lot of support to use this site."
    assert sus_items(qs) is None


def test_a_missing_item_means_it_is_not_sus():
    assert sus_items(_questions()[:9]) is None


def test_a_duplicated_item_means_it_is_not_sus():
    qs = _questions()
    qs.append({"id": "sus_again", "type": "multiple_choice", "prompt": SUS_ITEMS[0]})
    assert sus_items(qs) is None


def test_other_questions_may_sit_beside_the_scale():
    qs = _questions() + [{"id": "q_open", "type": "short_text", "prompt": "Co poprawić?"}]
    assert sus_items(qs) == ITEMS


@pytest.mark.parametrize("q_meta", [None, [], ["junk", 3], [{"id": "q1", "prompt": "Czy było łatwo?"}]])
def test_odd_questionnaires_are_not_sus(q_meta):
    assert sus_items(q_meta) is None


def test_polarity_marks_the_negative_sus_items():
    pol = polarity(_questions())
    assert [qid for qid, rev in pol.items() if rev] == ["sus2", "sus4", "sus6", "sus8", "sus10"]


def test_polarity_follows_the_marker_and_an_explicit_reverse_flag():
    qs = _questions(markers=True) + [
        {"id": "x", "prompt": "Strona była myląca.", "reverse": True},
        {"id": "y", "prompt": "Strona była jasna.", "reverse": False},
        {"id": "z", "prompt": "Strona była ładna."},
    ]
    pol = polarity(qs)
    assert pol["x"] is True and pol["y"] is False and pol["z"] is False
    assert pol["sus2"] is True and pol["sus3"] is False


def test_questions_from_manifest_accepts_a_questionnaire_stored_as_a_string():
    qs = _questions()
    assert questions_from_manifest({"questionnaire": {"questions": qs}}) == qs
    assert questions_from_manifest({"questionnaire": json.dumps({"questions": qs})}) == qs
    assert questions_from_manifest({"questionnaire": "{not json"}) == []
    assert questions_from_manifest({"questionnaire": {"questions": [qs[0], "junk"]}}) == [qs[0]]
    assert questions_from_manifest({}) == []


def test_reporter_and_dashboard_read_the_questionnaire_with_one_reader():
    assert reporter.questions_from_manifest is sus.questions_from_manifest
    assert results_api.questions_from_manifest is sus.questions_from_manifest


# --- the run's SUS figures ---------------------------------------------------

def test_two_scored_participants_give_a_mean_and_a_range():
    s = _sus([_report("julia", JULIA), _report("filip", FILIP)])
    assert [(p["persona"], p["score"]) for p in s["per_participant"]] == [("julia", 80.0), ("filip", 42.5)]
    assert (s["n_scored"], s["n_participants"]) == (2, 2)
    # 61.25 exactly: ties go up, as by hand. round() is half-to-even and gave 61.2.
    assert s["mean"] == 61.3
    assert (s["min"], s["max"]) == (42.5, 80.0)
    assert s["scale"] == "0-100"
    assert s["benchmark_human"] == 68 and s["benchmark_note"]


def test_failed_and_truncated_participants_are_listed_with_a_reason_not_a_score():
    reports = [
        _report("julia", JULIA),
        {**_report("filip", FILIP), "survey_result": {"error": "429 RESOURCE_EXHAUSTED"}},
        _report("short", [3] * 10, steps=2),
    ]
    stats = aggregate_statistics(reports, q_meta=_questions())
    s = stats["survey"]["sus"]
    assert (s["n_scored"], s["n_participants"]) == (1, 3)
    by_dir = {p["persona_dir"]: p for p in s["per_participant"]}
    assert by_dir["julia"]["score"] == 80.0 and by_dir["julia"]["why_not"] is None
    assert by_dir["filip"]["score"] is None and "nie powiodła się" in by_dir["filip"]["why_not"]
    assert by_dir["short"]["score"] is None and "przerwana" in by_dir["short"]["why_not"]
    # one person's score is not an average
    assert s["mean"] is None
    assert stats["survey"]["surveys_included"] == 1


def test_a_crashed_session_is_not_scored_even_after_many_steps():
    crashed = _report("crash", JULIA, steps=30)
    crashed["session_result"]["ended_reason"] = "error"
    s = _sus([crashed, _report("filip", FILIP)])
    assert s["n_scored"] == 1
    assert next(p for p in s["per_participant"] if p["persona_dir"] == "crash")["score"] is None


def test_a_participant_without_a_survey_is_listed_unscored():
    s = _sus([_report("julia", JULIA), {"trace_dir": "runs/r1/old", "survey_result": {}}])
    old = next(p for p in s["per_participant"] if p["persona_dir"] == "old")
    assert old["score"] is None and old["why_not"] == "brak ankiety"


def test_a_cannot_assess_placeholder_leaves_the_participant_unscored():
    report = _report("julia", JULIA)
    report["survey_result"]["answers"][6] = {
        "question_id": "sus7", "value": "3", "reason": "I cannot assess how quickly others learn."}
    p = _sus([report, _report("filip", FILIP)])["per_participant"][0]
    assert p["score"] is None and "9 z 10" in p["why_not"]


def test_an_out_of_scale_answer_leaves_the_participant_unscored_and_is_counted():
    report = _report("julia", JULIA)
    report["survey_result"]["answers"][6]["value"] = "9"
    stats = aggregate_statistics([report, _report("filip", FILIP)], q_meta=_questions())
    assert stats["survey"]["sus"]["per_participant"][0]["score"] is None
    assert stats["survey"]["invalid_answers"] == {"sus7": 1}
    q7 = next(q for q in stats["survey"]["questions"] if q["id"] == "sus7")
    assert q7["n"] == 1 and q7["invalid_answers"] == 1


def test_straight_lined_answers_are_flagged_not_removed():
    s = _sus([_report("flat", [3] * 10), _report("julia", JULIA)])
    assert s["straightlined"] == ["flat"]
    assert s["n_scored"] == 2
    assert next(p for p in s["per_participant"] if p["persona_dir"] == "flat")["score"] == 50.0


def test_survey_averages_stay_per_item_with_no_cross_item_mean():
    stats = aggregate_statistics([_report("julia", JULIA), _report("filip", FILIP)],
                                 q_meta=_questions())
    survey = stats["survey"]
    assert survey["avg_scores"]["sus1"] == 2.0 and survey["avg_scores"]["sus2"] == 2.5
    assert set(survey["avg_scores"]) == {f"sus{i}" for i in range(1, 11)}
    # nothing in the survey block is a single number averaged across the items
    assert not [k for k, v in survey.items() if isinstance(v, float)]
    assert set(survey["sus"]) >= {"mean", "n_scored", "per_participant"}


def test_the_key_is_omitted_when_the_questionnaire_is_not_sus():
    reports = [_report("julia", JULIA)]
    assert "sus" not in aggregate_statistics(reports)["survey"]
    assert "sus" not in aggregate_statistics(reports, q_meta=_questions()[:9])["survey"]


# --- the persona must not be primed -------------------------------------------

@pytest.mark.asyncio
async def test_run_survey_does_not_send_the_scoring_markers_to_the_model(store):
    store.write_memory_trace([])
    questions = [{**q, "reverse": q["sus_item"] % 2 == 0} for q in _questions(markers=True)]
    questionnaire = {"questionnaire_id": "sus", "questions": questions}
    sent = []

    async def capture(messages, **kwargs):
        sent.extend(messages)
        return json.dumps({"answers": []})

    with patch("simulated_web_agent.main.survey.async_chat", new=AsyncMock(side_effect=capture)):
        await run_survey(store, questionnaire=questionnaire)

    user = next(m["content"] for m in sent if m["role"] == "user")
    asked = json.loads(user.split("\n", 1)[0])["questions"]
    assert len(asked) == 10 + 2  # the ten items plus SEQ and confidence
    assert [q["id"] for q in asked[:10]] == [f"sus{i}" for i in range(1, 11)]
    assert "sus_item" not in user and "reverse" not in user
    assert asked[1]["prompt"] == SUS_ITEMS[1] and asked[1]["options"] == ["1", "2", "3", "4", "5"]
    # the caller's questionnaire (which goes to the manifest) keeps its markers
    assert questions[1]["sus_item"] == 2 and questions[1]["reverse"] is True


# --- every consumer of the polarity agrees with the score ----------------------------

@pytest.mark.parametrize("values", [JULIA, FILIP, [3] * 10, [5, 1] * 5, [1, 5] * 5])
def test_the_reverse_coded_average_is_the_sus_score_rescaled(values):
    """For a complete SUS the flipped mean is SUS / 25 + 1, so the satisfaction
    figure and the SUS score describe one participant and cannot disagree."""
    reverse = frozenset(qid for qid, rev in polarity(_questions()).items() if rev)
    survey = {"answers": [{"question_id": f"sus{i}", "value": str(v)}
                          for i, v in enumerate(values, start=1)]}
    score, _ = sus_score(_by_qid(values), ITEMS)
    assert _numeric_survey_avg(survey, reverse) == pytest.approx(score / 25 + 1, abs=0.005)


def test_a_persona_card_and_the_run_block_give_the_same_sus_score():
    run = _sus([_report("julia", JULIA), _report("filip", FILIP)])
    for name, values in (("julia", JULIA), ("filip", FILIP)):
        survey = {"answers": [{"question_id": f"sus{i}", "value": str(v), "reason": "r"}
                              for i, v in enumerate(values, start=1)]}
        card = results_api._dashboard_iso({"satisfaction": {}}, survey, _questions())["satisfaction"]
        row = next(p for p in run["per_participant"] if p["persona_dir"] == name)
        assert card["sus_score_0_100"] == row["score"]


def test_the_flags_and_the_run_block_come_from_one_polarity():
    stats = aggregate_statistics([_report("julia", JULIA)], q_meta=_questions())
    flagged = {q["id"] for q in stats["survey"]["questions"] if q.get("reverse")}
    assert flagged == {qid for qid, rev in polarity(_questions()).items() if rev}



def test_the_mean_rounds_ties_up_not_to_even():
    """Participant scores are multiples of 2.5, so two-person means land on a tie
    often: (80 + 42.5) / 2 = 61.25 and (77.5 + 40) / 2 = 58.75."""
    from simulated_web_agent.main.sus import _round_half_up

    assert _round_half_up(61.25) == 61.3
    assert _round_half_up(58.75) == 58.8
    assert _round_half_up(61.2) == 61.2
