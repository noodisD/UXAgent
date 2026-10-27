"""A placeholder answer is not a neutral rating.

The review found "Czułem/czułam kontrolę i bezpieczeństwo przy podawaniu danych
do płatności — śr. 3,00 / 5" on a car configurator where no participant ever
reached a payment step. The agents wrote "I cannot assess…" / "there was no
experience to form an opinion" and entered the scale midpoint as a placeholder,
and the average treated those 3s as lukewarm ratings. One answered "N/A" to a
1–5 question, which aggregation filed as an open-text answer.

On the hyundai run: 6 of 36 answers were placeholders; the payment and delivery
questions had no real answer from anyone.
"""
import pytest

from simulated_web_agent.main.reporter import _not_assessable, _rating, aggregate_statistics
from simulated_web_agent.main.reporter_html import _render_survey


@pytest.mark.parametrize("value,reason", [
    ("3", "The session concluded before any payment details were requested. Therefore, "
          "there was no experience to form an opinion on control or security."),
    ("3", "The memories do not contain any information about delivery, making it "
          "impossible to assess their clarity."),
    ("3", "I cannot assess feelings of control regarding payment data."),
    ("N/A", "The session did not reach any payment stage."),
    ("3", "Nie da się ocenić — sesja nie dotarła do płatności."),
    # Archived placeholders the first list missed (2026-09-24 hyundai, hebe).
    ("3", "I did not proceed to any payment pages, thus I cannot evaluate this aspect. "
          "Therefore, I chose a neutral option."),
    ("3", "My session was too short to assess this, so I chose a neutral option."),
    ("3", "I never accessed the actual website content. Therefore, I cannot judge the clarity."),
    ("3", "I had no experience to judge ease of task completion."),
])
def test_placeholders_are_recognised(value, reason):
    assert _not_assessable(value, reason) is True


@pytest.mark.parametrize("value,reason", [
    ("3", "The total price was displayed, but delivery costs were not mentioned."),
    ("2", "Memories reveal difficulty comparing trims."),
    ("5", "Everything was clear."),
    # Measured and deliberately not markers: both phrases also carry ratings.
    ("3", "Navigation and form filling undermine reliability, leading to a neutral "
          "rating as a compromise."),
    ("4", "While I did not reach a final payment stage, the costs presented up to the "
          "summary were transparent."),
])
def test_real_ratings_are_kept(value, reason):
    """A 3 with a reason is a rating. Over-matching here would empty the survey."""
    assert _not_assessable(value, reason) is False


def _report(qid, value, reason):
    return {"survey_result": {"answers": [{"question_id": qid, "value": value, "reason": reason}]}}


def test_placeholders_stay_out_of_the_average():
    reports = [_report("e6", "3", "I cannot assess this."), _report("e6", "5", "Clear.")]
    q = next(q for q in aggregate_statistics(reports)["survey"]["questions"] if q["id"] == "e6")
    assert q["avg"] == 5.0 and q["n"] == 1 and q["not_assessable"] == 1


def test_a_question_nobody_could_answer_is_shown_not_dropped():
    reports = [_report("e6", "3", "I cannot assess this."), _report("e6", "N/A", "n/a")]
    stats = aggregate_statistics(reports)
    q = next(q for q in stats["survey"]["questions"] if q["id"] == "e6")
    assert q["kind"] == "not_assessable" and q["not_assessable"] == 2
    assert not stats["survey"]["open_answers"].get("e6"), "'N/A' is not an open answer"


LIKERT = {"id": "q1", "type": "multiple_choice", "prompt": "Łatwo było znaleźć produkt",
          "options": ["1", "2", "3", "4", "5"]}


def _question(stats, qid="q1"):
    return next(q for q in stats["survey"]["questions"] if q["id"] == qid)


def test_a_value_outside_the_options_is_not_a_rating():
    """9 and 0 on a 1-5 item used to enter the average and n while the
    distribution dropped them, so the three disagreed."""
    reports = [_report("q1", "5", "Jasne."), _report("q1", "9", "Świetnie."),
               _report("q1", "0", "Fatalnie.")]
    stats = aggregate_statistics(reports, q_meta=[LIKERT])
    q = _question(stats)
    assert (q["avg"], q["n"], q["invalid_answers"]) == (5.0, 1, 2)
    assert sum(d["count"] for d in q["distribution"]) == q["n"]
    assert stats["survey"]["invalid_answers"] == {"q1": 2}
    assert stats["survey"]["avg_scores"] == {"q1": 5.0}
    assert not stats["survey"]["open_answers"].get("q1"), "a number is not an open answer"


def test_a_participant_with_only_invalid_answers_is_not_an_included_survey():
    stats = aggregate_statistics([_report("q1", "9", "Świetnie."), _report("q1", "4", "Dobrze.")],
                                 q_meta=[LIKERT])
    assert stats["survey"]["surveys_included"] == 1
    assert stats["survey"]["invalid_answers"] == {"q1": 1}


@pytest.mark.parametrize("val,meta,qid,expected", [
    ("4", LIKERT, "q1", 4.0),
    (4, LIKERT, "q1", 4.0),
    ("9", LIKERT, "q1", None),
    ("0", LIKERT, "q1", None),
    ("2.5", LIKERT, "q1", None),
    ("Tak", LIKERT, "q1", None),
    ("3", {"id": "q4", "type": "short_text"}, "q4", None),
    ("3", {"id": "q2", "options": ["Tak", "Nie"]}, "q2", 3.0),
    ("12", None, "x1", 12.0),
    ("nan", None, "x1", None),
    (float("inf"), None, "x1", None),
    (None, None, "x1", None),
    ("7", None, "synthetic_seq", 7.0),
    ("8", None, "synthetic_seq", None),
])
def test_rating(val, meta, qid, expected):
    assert _rating(val, meta, qid) == expected


def test_a_number_typed_into_a_free_text_question_is_an_open_answer():
    meta = {"id": "q4", "type": "short_text", "prompt": "Co było najtrudniejsze?"}
    stats = aggregate_statistics([_report("q4", "3", "Trzy rzeczy."), _report("q4", "Filtry", "Za dużo.")],
                                 q_meta=[meta])
    q = _question(stats, "q4")
    assert q["kind"] == "open" and q["answers"] == ["3", "Filtry"]
    assert "q4" not in stats["survey"]["avg_scores"]
    assert stats["survey"]["invalid_answers"] == {}


def test_a_question_with_label_options_keeps_its_answers_on_the_open_path():
    meta = {"id": "q2", "type": "multiple_choice", "prompt": "Czy polecisz?", "options": ["Tak", "Nie"]}
    stats = aggregate_statistics([_report("q2", "Tak", "Bo tak."), _report("q2", "Nie", "Bo nie.")],
                                 q_meta=[meta])
    q = _question(stats, "q2")
    assert q["kind"] == "open" and q["answers"] == ["Tak", "Nie"]
    assert stats["survey"]["invalid_answers"] == {}


def test_without_question_metadata_any_finite_number_is_still_a_rating():
    stats = aggregate_statistics([_report("x1", "9", "Ok."), _report("x1", "nan", "Ok."),
                                  _report("x1", "inf", "Ok.")])
    assert stats["survey"]["avg_scores"] == {"x1": 9.0}
    assert stats["survey"]["invalid_answers"] == {"x1": 2}


def test_the_synthetic_scales_run_to_seven_not_five():
    stats = aggregate_statistics([_report("synthetic_seq", "7", "Łatwo."),
                                  _report("synthetic_seq", "8", "Łatwo."),
                                  _report("synthetic_seq", "0", "Trudno.")])
    q = _question(stats, "synthetic_seq")
    assert (q["avg"], q["n"], q["invalid_answers"]) == (7.0, 1, 2)


def test_a_placeholder_is_counted_as_not_assessable_not_as_invalid():
    stats = aggregate_statistics([_report("q1", "9", "I cannot assess this.")], q_meta=[LIKERT])
    assert stats["survey"]["not_assessable"] == {"q1": 1}
    assert stats["survey"]["invalid_answers"] == {}


def test_the_report_says_so():
    html = _render_survey({"survey": {"questions": [
        {"id": "e6", "prompt": "Płatność", "kind": "not_assessable", "not_assessable": 3},
        {"id": "e4", "prompt": "Koszty", "kind": "scale", "min": 1, "max": 5,
         "low_label": "nie", "high_label": "tak", "avg": 4.0, "n": 2, "not_assessable": 1,
         "distribution": [{"value": v, "count": 1 if v == 4 else 0} for v in range(1, 6)]},
    ]}})
    assert "brak ocen" in html
    assert "1 bez oceny" in html
