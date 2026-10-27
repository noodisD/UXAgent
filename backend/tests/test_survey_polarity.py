"""Every place a survey number is shown or narrated reads the negative items the right way round.

Half of the SUS items are worded negatively ("I found the site unnecessarily
complex"), so for them a high average is the bad result. The report coloured
them on a green-high scale, the AI summaries and the aggregate narrator read
all ten the same way, and the satisfaction figure averaged the raw answers —
a raw 2,15 of 5 was narrated as "bardzo nisko" for participants whose SUS was
80 and 42,5.
"""
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document

from simulated_web_agent.main import results_api
from simulated_web_agent.main.analysis_runner import run_all_analyses
from simulated_web_agent.main.iso_metrics import _numeric_survey_avg, compute_iso_metrics, reversed_ids
from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter import (
    _summarize_survey, _survey_for_prompt, _survey_legend, aggregate_statistics,
    generate_aggregated_report,
)
from simulated_web_agent.main.reporter_html import (
    REVERSED_TAG, _render_scale_question, _render_survey, _scale_color, generate_html_report,
    survey_notes,
)
from simulated_web_agent.main.trace_store import TraceStore
from tests.conftest import make_agent_report
from tests.test_sus_scoring import FILIP, JULIA, _questions, _report

SRC = Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
GOOD, BAD = "#008573", "#E60000"


def _stats(extra=()):
    return aggregate_statistics([_report("Julia", JULIA), _report("Filip", FILIP), *extra],
                                q_meta=_questions())


def _failed(name):
    return {**_report(name, JULIA), "survey_result": {"error": "429 RESOURCE_EXHAUSTED: quota"}}


def _scale(**over):
    return {"id": "sus2", "prompt": "I found the site unnecessarily complex.", "kind": "scale",
            "min": 1, "max": 5, "low_label": "zdecydowanie nie", "high_label": "zdecydowanie tak",
            "avg": 4.0, "n": 2, "distribution": [{"value": v, "count": 2 if v == 4 else 0}
                                                 for v in range(1, 6)], **over}


# --- the colour scale --------------------------------------------------------

def test_a_high_average_on_a_reversed_item_is_not_coloured_as_good():
    assert _scale_color(4.0, 1, 5) == GOOD
    assert _scale_color(4.0, 1, 5, reverse=True) == BAD
    assert _scale_color(1.0, 1, 5, reverse=True) == GOOD
    assert _scale_color(2.0, 1, 5, reverse=True) == _scale_color(4.0, 1, 5)


def test_reversing_keeps_the_scale_own_range_and_the_unknown_grey():
    assert _scale_color(6.0, 1, 7, reverse=True) == BAD
    assert _scale_color(None, 1, 5, reverse=True) == _scale_color(None, 1, 5)


def test_a_reversed_question_carries_the_tag_and_the_flipped_colour():
    html = _render_scale_question(_scale(reverse=True))
    assert REVERSED_TAG == "pytanie odwrócone: niżej = lepiej" and REVERSED_TAG in html
    assert BAD in html and GOOD not in html
    plain = _render_scale_question(_scale())
    assert REVERSED_TAG not in plain and GOOD in plain


# --- the questions carry the flag, the legend says it -----------------------------

def test_the_even_sus_items_are_flagged_reverse():
    questions = {q["id"]: q for q in _stats()["survey"]["questions"]}
    assert [questions[f"sus{i}"].get("reverse", False) for i in range(1, 11)] == [False, True] * 5


def test_a_question_marked_reverse_is_flagged_and_an_ordinary_one_is_not():
    q_meta = [{"id": "q1", "prompt": "Strona była myląca.", "reverse": True},
              {"id": "q2", "prompt": "Strona była jasna."}]
    report = {"trace_dir": "runs/r1/a", "persona": "Persona: Ala",
              "survey_result": {"answers": [{"question_id": "q1", "value": "4", "reason": "r"},
                                            {"question_id": "q2", "value": "4", "reason": "r"}]},
              "session_result": {"steps_taken": 12, "ended_reason": "terminated"}}
    by_id = {q["id"]: q for q in aggregate_statistics([report], q_meta=q_meta)["survey"]["questions"]}
    assert by_id["q1"]["reverse"] is True and "reverse" not in by_id["q2"]


def test_the_legend_marks_the_reversed_questions():
    lines = dict(line[2:].split(" = ", 1) for line in _survey_legend(_stats()["survey"]).splitlines())
    assert lines["sus2"].endswith("(skala 1–5; odwrócone: wyższa średnia = gorzej)")
    assert lines["sus1"].endswith("(skala 1–5)")
    assert lines["sus9"].endswith("(skala 1–5)")


# --- the sentences above the questions -----------------------------------------

def test_the_report_prints_the_sus_line_and_the_reversed_tags():
    html = _render_survey(_stats())
    assert ("SUS: 61,3/100 (n=2 z 2; zakres 42,5–80,0). Syntetyczna samoocena person; "
            "benchmark 68 dotyczy ludzi — kontekst, nie porównanie.") in html
    assert html.count(REVERSED_TAG) == 5
    assert html.index("SUS: 61,3") < html.index("sv-q-head"), "the line sits above the questions"


def test_a_run_without_sus_prints_none_of_it():
    stats = aggregate_statistics([_report("Julia", JULIA)])
    assert survey_notes(stats) == []
    assert "SUS" not in _render_survey(stats) and REVERSED_TAG not in _render_survey(stats)


def test_below_two_scored_participants_the_line_says_how_many_were_scored():
    [line] = survey_notes(aggregate_statistics([_report("Julia", JULIA)], q_meta=_questions()))
    assert line.startswith("SUS: bez średniej — wynik policzono dla 1 z 1 uczestników (80,0/100).")
    assert "/100 (n=" not in line


def test_whoever_has_no_sus_score_is_listed_with_the_reason():
    stats = _stats(extra=[_report("Ola", [3] * 10, steps=2)])
    sus_line, unscored = survey_notes(stats)
    assert sus_line.startswith("SUS: 61,3/100 (n=2 z 3;")
    assert unscored == ("Bez wyniku SUS: Ola (sesja przerwana po 2 krokach — "
                        "ankieta wykluczona ze średnich).")


def test_a_failed_survey_is_named_once_with_what_it_means_for_the_averages():
    stats = _stats(extra=[_failed("Marta")])
    sus_line, failed = survey_notes(stats)
    assert sus_line.startswith("SUS: 61,3/100 (n=2 z 3;")
    assert failed == ("Ankieta nie powiodła się dla: Marta — jej odpowiedzi nie wchodzą do średnich "
                      "(n=2 z 3).")
    html = _render_survey(stats)
    assert failed in html and "Bez wyniku SUS" not in html, "named below, not twice"


def test_several_failed_surveys_use_the_plural():
    *_, failed = survey_notes(_stats(extra=[_failed("Marta"), _failed("Ola")]))
    assert failed.startswith("Ankiety nie powiodły się dla: Marta, Ola — ich odpowiedzi nie wchodzą")


def test_when_every_survey_failed_the_section_still_says_so():
    stats = aggregate_statistics([_failed("Marta")], q_meta=_questions())
    assert not stats["survey"]["questions"]
    html = _render_survey(stats)
    assert "Wyniki ankiety" in html and "Ankieta nie powiodła się dla: Marta" in html


def _agg(stats):
    return {"website": "x.pl", "task": "Zadanie",
            "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                          "serious_issues": [], "minor_issues": [], "priority_recommendations": []},
            "statistics": {"participant_count": stats["participant_count"], "heuristic": {},
                           "survey": stats["survey"]}}


def test_the_whole_html_report_carries_the_line():
    html = generate_html_report(_agg(_stats(extra=[_failed("Marta")])))
    assert "SUS: 61,3/100 (n=2 z 3; zakres 42,5–80,0)" in html
    assert "Ankieta nie powiodła się dla: Marta" in html


def test_the_word_file_carries_the_same_lines(tmp_path):
    out = build_docx(_agg(_stats(extra=[_failed("Marta"), _report("Ola", [3] * 10, steps=2)])),
                     tmp_path, tmp_path / "r.docx")
    text = "\n".join(p.text for p in Document(out).paragraphs)
    assert "SUS: 61,3/100 (n=2 z 4; zakres 42,5–80,0). Syntetyczna samoocena person;" in text
    assert "Bez wyniku SUS: Ola (sesja przerwana po 2 krokach" in text
    assert "Ankieta nie powiodła się dla: Marta — jej odpowiedzi nie wchodzą do średnich (n=2 z 4)." in text
    assert text.count(REVERSED_TAG) == 5
    assert text.index("SUS: 61,3") < text.index("I think I would like to use this site frequently.")


def test_the_word_file_with_only_failed_surveys_still_has_the_section(tmp_path):
    out = build_docx(_agg(aggregate_statistics([_failed("Marta")], q_meta=_questions())),
                     tmp_path, tmp_path / "r.docx")
    doc = Document(out)
    assert "Wyniki ankiety" in [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert any("Ankieta nie powiodła się dla: Marta" in p.text for p in doc.paragraphs)


# --- the AI "why they answered" summaries ------------------------------------------

def _third_participant():
    """Answers sus2 with a placeholder 3 and sus4 with a 9, the rest as Julia."""
    r = _report("Ola", JULIA)
    r["survey_result"]["answers"][1] = {"question_id": "sus2", "value": "3",
                                        "reason": "I cannot assess this."}
    r["survey_result"]["answers"][3] = {"question_id": "sus4", "value": "9", "reason": "Off the scale."}
    return r


async def _summary_call(reports, q_meta):
    chat = AsyncMock(return_value=json.dumps({"summaries": {}}))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        await _summarize_survey(reports, q_meta)
    system, user = chat.call_args.args[0]
    return system["content"], json.loads(user["content"])


@pytest.mark.asyncio
async def test_the_summary_payload_flags_a_reversed_item_and_says_how_to_read_it():
    system, payload = await _summary_call([_report("Julia", JULIA), _report("Filip", FILIP)], _questions())
    assert payload["sus2"]["uwaga"] == "pytanie negatywne — niska średnia oznacza ocenę dobrą"
    assert "uwaga" not in payload["sus1"]
    assert payload["sus2"]["średnia"] == 2.5
    assert "other way round" in system and "LOW mean is a good assessment" in system
    assert "the tone must agree with it" in system


@pytest.mark.asyncio
async def test_the_summary_mean_is_the_average_the_report_prints():
    reports = [_report("Julia", JULIA), _report("Filip", FILIP), _third_participant()]
    _, payload = await _summary_call(reports, _questions())
    printed = {q["id"]: q for q in aggregate_statistics(reports, q_meta=_questions())["survey"]["questions"]}
    for qid in (f"sus{i}" for i in range(1, 11)):
        assert payload[qid]["średnia"] == printed[qid]["avg"], qid
        assert len(payload[qid]["odpowiedzi"]) == printed[qid]["n"], qid
    assert printed["sus4"]["n"] == 2 and payload["sus4"]["średnia"] == 1.0, "the 9 is not an answer"


@pytest.mark.asyncio
async def test_a_placeholder_is_not_an_answer_to_summarise():
    reports = [_report("Julia", JULIA), _report("Filip", FILIP), _third_participant()]
    _, payload = await _summary_call(reports, _questions())
    assert len(payload["sus2"]["odpowiedzi"]) == 2
    everything = json.dumps(payload, ensure_ascii=False)
    assert "cannot assess" not in everything and "Off the scale" not in everything


@pytest.mark.asyncio
async def test_a_question_nobody_could_answer_is_not_summarised():
    only_placeholders = [{**_report("Ola", JULIA), "survey_result": {"answers": [
        {"question_id": "sus1", "value": "3", "reason": "I cannot assess this."}]}}]
    chat = AsyncMock()
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        assert await _summarize_survey(only_placeholders, _questions()) == []
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_a_number_typed_into_a_free_text_question_is_still_an_answer_to_summarise():
    open_q = {"id": "e1", "type": "short_text", "prompt": "Co poprawić?"}
    report = _report("Julia", JULIA)
    report["survey_result"]["answers"].append({"question_id": "e1", "value": "3", "reason": "Raty."})
    _, payload = await _summary_call([report], _questions() + [open_q])
    assert isinstance(payload["e1"], list) and "odpowiedź: 3" in payload["e1"][0], \
        "a number typed into a free-text question is an answer, not a rating"


# --- the narrator ---------------------------------------------------------------------

def test_the_narrator_survey_data_keeps_sus_and_reverse_but_not_the_tools_error_text():
    stats = _stats(extra=[_failed("Marta")])
    for_prompt = _survey_for_prompt(stats["survey"])
    dumped = json.dumps(for_prompt, ensure_ascii=False)
    assert "RESOURCE_EXHAUSTED" not in dumped
    assert for_prompt["failed_surveys"][0]["persona"] == "Marta"
    assert for_prompt["sus"]["n_scored"] == 2 and '"reverse": true' in dumped
    assert "error" in stats["survey"]["failed_surveys"][0], "the stored statistics keep the text"
    assert _survey_for_prompt(None) == {} and _survey_for_prompt({"questions": []}) == {"questions": []}


def test_the_aggregate_prompt_still_formats_and_states_the_polarity_rules():
    """The prompt goes through str.format: one stray brace in the new text loses the whole report."""
    prompt = (SRC / "audit_prompts" / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    reporter_src = (SRC / "main" / "reporter.py").read_text(encoding="utf-8")
    call = reporter_src[reporter_src.index("prompt_template.format("):]
    given = set(re.findall(r"^\s+([a-z_]+)=", call[:call.index("\n    )\n")], re.M))
    out = prompt.format(**{k: f"<{k}>" for k in given})
    rules = prompt[prompt.index("WAŻNE (ankieta, pytania odwrócone i SUS)"):prompt.index("## Output format")]
    assert "{" not in rules and "}" not in rules, "the rules name keys in backticks and need no braces"
    assert "WYŻSZA średnia oznacza GORSZĄ ocenę" in out and "`reverse: true`" in out
    assert "Wynik SUS (skala 0–100) bierz WYŁĄCZNIE z `survey_summary.sus`" in out
    assert "Nie uśredniaj surowych odpowiedzi z RÓŻNYCH pytań" in out
    assert "Pole `failed_surveys` wymienia uczestników, których ankiet narzędzie nie zdołało zebrać" in out


@pytest.mark.asyncio
async def test_the_narrator_and_the_summaries_are_handed_the_sus_block_and_the_polarity(tmp_path):
    stores = []
    for name, answers in (("Julia", JULIA), ("Filip", FILIP)):
        store = TraceStore(tmp_path / name)
        report = make_agent_report()
        report["persona"] = f"Persona: {name}\nWiek: 30"
        report["survey_result"] = _report(name, answers)["survey_result"]
        report["session_result"] = {"steps_taken": 12, "ended_reason": "terminated"}
        store.write_ux_report(report)
        stores.append(store)
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"questionnaire": {"questions": _questions()}}), encoding="utf-8")
    narrative = {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                 "serious_issues": [], "minor_issues": [], "priority_recommendations": []}
    chat = AsyncMock(return_value=json.dumps(narrative))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        result = await generate_aggregated_report(stores=stores, task_description="Kup buty",
                                                  website_url="https://example.com")

    sent = [c.args[0] for c in chat.await_args_list]
    narrator = next(m[-1]["content"] for m in sent if "SURVEY QUESTIONNAIRE RESULTS" in m[-1]["content"])
    assert '"sus": {' in narrator and '"n_scored": 2' in narrator and '"reverse": true' in narrator
    assert "(skala 1–5; odwrócone: wyższa średnia = gorzej)" in narrator
    summaries = next(m for m in sent if len(m) == 2 and m[0]["role"] == "system"
                     and "uwaga" in m[1]["content"])
    assert json.loads(summaries[1]["content"])["sus2"]["uwaga"]
    html = (tmp_path / "aggregated_report.html").read_text(encoding="utf-8")
    assert "SUS: 61,3/100 (n=2 z 2" in html and html.count(REVERSED_TAG) == 5
    assert result["statistics"]["survey"]["sus"]["mean"] == pytest.approx(61.3, abs=0.01)


# --- the satisfaction figure ------------------------------------------------------------

def _answers(values):
    return {"answers": [{"question_id": f"sus{i}", "value": str(v), "reason": "r"}
                        for i, v in enumerate(values, start=1)]}


def test_the_raw_average_is_two_point_two_and_the_reverse_coded_one_is_four_point_two():
    survey = _answers(JULIA)
    assert _numeric_survey_avg(survey) == 2.2
    assert _numeric_survey_avg(survey, reversed_ids(_questions())) == 4.2
    assert _numeric_survey_avg(_answers(FILIP), reversed_ids(_questions())) == 2.7


def test_only_the_reversed_questions_are_flipped_and_the_seq_stays_out():
    survey = {"answers": [{"question_id": "a", "value": "4"}, {"question_id": "b", "value": "4"},
                          {"question_id": "synthetic_seq", "value": "2"}]}
    assert _numeric_survey_avg(survey, frozenset({"b"})) == 3.0
    assert _numeric_survey_avg(survey) == 4.0


def test_a_persona_in_a_run_gets_the_reverse_coded_score(tmp_path):
    store = TraceStore(tmp_path / "julia")
    store.write_survey_result(_answers(JULIA))
    store.write_objective_reflection({"outcome": "success"})
    store.write_session_result({"steps_taken": 12, "ended_reason": "terminated"})
    assert compute_iso_metrics(store)["satisfaction"]["synthetic_score_1_5"] == 2.2
    assert compute_iso_metrics(store, _questions())["satisfaction"]["synthetic_score_1_5"] == 4.2


@pytest.mark.asyncio
async def test_the_analysis_runner_hands_the_questions_to_the_iso_pass(store):
    questions = _questions()
    seen = []
    patches = {n: AsyncMock(return_value={}) for n in (
        "run_survey", "run_audit_analysis", "run_viewport_scan", "run_link_check",
        "run_cognitive_walkthrough", "generate_per_agent_report", "generate_objective_reflection",
        "run_bias_audit")}
    with patch.multiple("simulated_web_agent.main.analysis_runner", **patches), \
            patch("simulated_web_agent.main.analysis_runner.run_iso_metrics",
                  side_effect=lambda *a: seen.append(a)):
        await run_all_analyses(store, {"questions": questions}, headless=True)
        await run_all_analyses(store, {"q1": "?"}, headless=True)
    assert seen == [(store, questions), (store, None)]


# --- the dashboard ------------------------------------------------------------------------

def _iso():
    return {"satisfaction": {"synthetic_score_1_5": 2.2, "synthetic_seq_1_7": 5.0}}


def test_the_dashboard_recomputes_the_score_with_polarity_even_without_placeholders():
    sat = results_api._dashboard_iso(_iso(), _answers(JULIA), _questions())["satisfaction"]
    assert sat["synthetic_score_1_5"] == 4.2 and sat["sus_score_0_100"] == 80.0
    assert sat["synthetic_seq_1_7"] == 5.0 and "not_assessable" not in sat
    # SUS / 25 + 1: the two figures describe the same participant
    assert sat["synthetic_score_1_5"] == sat["sus_score_0_100"] / 25 + 1


def test_without_a_questionnaire_the_dashboard_keeps_the_plain_average_and_adds_no_sus():
    sat = results_api._dashboard_iso(_iso(), _answers(JULIA))["satisfaction"]
    assert sat["synthetic_score_1_5"] == 2.2 and "sus_score_0_100" not in sat


def test_an_incomplete_sus_gets_no_score_but_still_the_reverse_coded_average():
    survey = _answers(JULIA)
    survey["answers"][6] = {"question_id": "sus7", "value": "3", "reason": "I cannot assess this."}
    sat = results_api._dashboard_iso(_iso(), survey, _questions())["satisfaction"]
    assert "sus_score_0_100" not in sat
    assert sat["not_assessable"] == 1 and sat["synthetic_score_1_5"] == pytest.approx(4.22, abs=0.005)


def test_the_dashboard_can_leave_the_sus_score_out():
    sat = results_api._dashboard_iso(_iso(), _answers(JULIA), _questions(), include_sus=False)["satisfaction"]
    assert "sus_score_0_100" not in sat and sat["synthetic_score_1_5"] == 4.2


def _persona(tmp_path, steps):
    store = TraceStore(tmp_path / "julia")
    store.write_survey_result(_answers(JULIA))
    store.write_session_result({"steps_taken": steps, "ended_reason": "terminated"})
    store.write_iso_metrics({"effectiveness": {}, "satisfaction": _iso()["satisfaction"]})
    return store.trace_dir


def test_a_persona_card_shows_the_sus_score(tmp_path):
    sat = results_api.build_persona(_persona(tmp_path, 12), {}, _questions())["iso"]["satisfaction"]
    assert sat["sus_score_0_100"] == 80.0 and sat["synthetic_score_1_5"] == 4.2


def test_a_truncated_session_has_no_sus_on_the_card_because_the_report_leaves_it_out(tmp_path):
    sat = results_api.build_persona(_persona(tmp_path, 2), {}, _questions())["iso"]["satisfaction"]
    assert "sus_score_0_100" not in sat


def test_the_run_survey_block_passes_the_sus_figures_through(tmp_path):
    stats = _stats()
    report = {"statistics": {"survey": stats["survey"]}}
    survey = results_api._run_survey(tmp_path, report, _questions())
    assert survey["sus"] == stats["survey"]["sus"] and survey["sus"]["n_scored"] == 2
    assert all(q.get("reverse") for q in survey["questions"] if q["id"] in ("sus2", "sus10"))


def test_an_older_report_has_no_sus_to_pass_through(tmp_path):
    old = {k: v for k, v in _stats()["survey"].items() if k != "sus"}
    assert results_api._run_survey(tmp_path, {"statistics": {"survey": old}}, [])["sus"] is None
