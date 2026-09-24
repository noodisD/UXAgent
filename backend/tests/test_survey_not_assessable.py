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

from simulated_web_agent.main.reporter import _not_assessable, aggregate_statistics
from simulated_web_agent.main.reporter_html import _render_survey


@pytest.mark.parametrize("value,reason", [
    ("3", "The session concluded before any payment details were requested. Therefore, "
          "there was no experience to form an opinion on control or security."),
    ("3", "The memories do not contain any information about delivery, making it "
          "impossible to assess their clarity."),
    ("3", "I cannot assess feelings of control regarding payment data."),
    ("N/A", "The session did not reach any payment stage."),
    ("3", "Nie da się ocenić — sesja nie dotarła do płatności."),
])
def test_placeholders_are_recognised(value, reason):
    assert _not_assessable(value, reason) is True


@pytest.mark.parametrize("value,reason", [
    ("3", "The total price was displayed, but delivery costs were not mentioned."),
    ("2", "Memories reveal difficulty comparing trims."),
    ("5", "Everything was clear."),
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


def test_the_report_says_so():
    html = _render_survey({"survey": {"questions": [
        {"id": "e6", "prompt": "Płatność", "kind": "not_assessable", "not_assessable": 3},
        {"id": "e4", "prompt": "Koszty", "kind": "scale", "min": 1, "max": 5,
         "low_label": "nie", "high_label": "tak", "avg": 4.0, "n": 2, "not_assessable": 1,
         "distribution": [{"value": v, "count": 1 if v == 4 else 0} for v in range(1, 6)]},
    ]}})
    assert "brak ocen" in html
    assert "1 bez oceny" in html
