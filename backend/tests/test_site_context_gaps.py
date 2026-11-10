"""One site_context default, one helper, and the gaps a run verified.

first_impression had a default for an undeclared site, heuristics had a blank and the
reflection "(nie podano)": the same site was a shop to one pass and not to another. The
helper (site_scope.site_context_for) is the one place that decides, and it appends what
the run verified the task presupposed and the sessions did not find (premise_check).
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document

from simulated_web_agent.main.auditor import _heuristic_pass, _run_first_impression
from simulated_web_agent.main.premise_check import write_premise_check
from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter import (
    generate_aggregated_report, generate_objective_reflection, resolve_narrative,
)
from simulated_web_agent.main.site_scope import UNDECLARED_SITE_CONTEXT
from simulated_web_agent.main.survey import run_survey
from simulated_web_agent.main.trace_store import TraceStore
from tests.conftest import make_agent_report
from tests.test_site_context_threading import (
    _REFLECTION_REPLY, _REPLY, _reflection_store, _sent_text, _store,
)

GAP = ("W 3 sesjach na 6 stronach produktów nie zaobserwowano: odbiór zamówienia w sklepie. "
       "Serwis sam zastrzega: „Oferta w sklepie może się różnić ceną”.")
# What premise_check keeps when the site's own copy backs the gap (two pages or two people).
SITE_SAYS = [{"persona_dir": "p0", "step": 0, "quote": "Oferta w sklepie może się różnić ceną"}]
# The line site_context_for adds (the prompts only quote its first words, in quotation marks).
GAP_LINE = "Zadanie zakłada funkcję „"
PICKUP_INTENT = "Sprawdź, czy poduszka jest dostępna do odbioru w sklepie"


def _with_gap(store):
    """The run directory above `store` says the task's pickup was not found."""
    store.write_basic_info({**(store.read_basic_info() or {}), "intent": PICKUP_INTENT})
    write_premise_check(store.trace_dir.parent, {"version": 1, "premises": {
        "pickup": {"status": "absent", "text": GAP, "evidence": SITE_SAYS}}})


def _heuristic_store(tmp_path, site_context=""):
    store = TraceStore(tmp_path / "persona")
    store.write_basic_info({"site_context": site_context, "intent": PICKUP_INTENT,
                            "persona": "Julia woli sklepy stacjonarne."})
    store.write_memory_trace([{"kind": "reflection", "content": "Nie widzę odbioru.",
                               "timestamp": 1, "importance": 0.5}])
    store.write_action_trace([{"description": "klik", "action": "click"}])
    store.append_observation({"tabs": [{"url": "https://pepco.pl/", "is_active": True}]})
    store.write_observation_step_text(0, "Strona główna.")
    return store


async def _sent_to_every_pass(tmp_path, site_context="", gap=False):
    """What each pass that reads site_context was sent, as {pass name: text}."""
    stores = {
        "first_impression": _store(tmp_path / "fi", site_context=site_context),
        "heuristics": _heuristic_store(tmp_path / "he", site_context),
        "reflection": _reflection_store(tmp_path / "re", site_context=site_context),
        "survey": _store(tmp_path / "su", site_context=site_context),
    }
    if gap:
        for store in stores.values():
            _with_gap(store)
    sent = {}
    mock = AsyncMock(return_value=_REPLY)
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        await _run_first_impression(stores["first_impression"])
    sent["first_impression"] = _sent_text(mock)

    mock = AsyncMock(return_value="[]")
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        await _heuristic_pass(stores["heuristics"])
    sent["heuristics"] = mock.await_args.args[0][0]["content"]

    mock = AsyncMock(return_value=_REFLECTION_REPLY)
    with patch("simulated_web_agent.main.reporter.async_chat", new=mock):
        await generate_objective_reflection(stores["reflection"])
    sent["reflection"] = mock.await_args.args[0][1]["content"]

    mock = AsyncMock(return_value='{"answers": []}')
    with patch("simulated_web_agent.main.survey.async_chat", new=mock):
        await run_survey(stores["survey"], {"questions": []})
    sent["survey"] = mock.await_args.args[0][0]["content"]
    return sent


@pytest.mark.asyncio
async def test_an_undeclared_context_is_one_sentence_in_every_pass(tmp_path):
    sent = await _sent_to_every_pass(tmp_path)
    for name in ("first_impression", "heuristics", "reflection"):
        assert UNDECLARED_SITE_CONTEXT in sent[name], name
    # The persona answers as itself: no block telling it how to judge an undeclared site.
    assert "SITE CONTEXT" not in sent["survey"]


@pytest.mark.asyncio
async def test_a_declared_context_is_passed_verbatim_to_every_pass(tmp_path):
    sent = await _sent_to_every_pass(tmp_path, site_context="Katalog bez zakupów online.")
    for name, text in sent.items():
        assert "Katalog bez zakupów online." in text, name
        assert UNDECLARED_SITE_CONTEXT not in text, name
        assert GAP_LINE not in text, name


@pytest.mark.asyncio
async def test_the_verified_gaps_reach_every_pass_for_the_task_that_needs_them(tmp_path):
    sent = await _sent_to_every_pass(tmp_path, site_context="Sklep.", gap=True)
    for name, text in sent.items():
        assert GAP_LINE in text, name
        assert "odbiór zamówienia w sklepie" in text, name
        assert "Oferta w sklepie może się różnić" in text, name


@pytest.mark.asyncio
async def test_the_heuristic_prompt_carries_the_persona_and_the_rules_about_habits(tmp_path):
    store = _heuristic_store(tmp_path, "Sklep.")
    mock = AsyncMock(return_value="[]")
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        await _heuristic_pass(store)
    prompt = mock.await_args.args[0][0]["content"]
    assert "NOT evidence about the site" in prompt
    assert "Julia woli sklepy stacjonarne." in prompt
    assert "follows from the persona's own preference" in prompt
    assert "limit inherent to the medium" in prompt
    # ... and what the site could say and does not stays reportable
    assert "information the site\ncould supply and withholds" in prompt
    assert "{persona_disposition}" not in prompt


# --- the aggregate report ----------------------------------------------------------

def _aggregate_run(tmp_path, declared=""):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"start_url": "https://pepco.pl/"}), encoding="utf-8")
    stores = []
    for i in range(2):
        s = TraceStore(tmp_path / f"p{i}")
        s.write_basic_info({"intent": PICKUP_INTENT, "site_context": declared})
        report = make_agent_report()
        report["trace_dir"] = str(s.trace_dir)
        s.write_ux_report(report)
        s.write_action_trace([json.dumps({"action": "click", "description": "c"})])
        s.append_observation({"tabs": [{"url": "https://pepco.pl/products/poduszka-1?Colour=a",
                                        "is_active": True}]})
        s.write_observation_step_text(0, "Poduszka. 20 zł.")
        stores.append(s)
    return stores


_NARRATIVE = json.dumps({"executive_summary": "Badanie z udziałem 2 uczestników.",
                         "key_strengths": [], "critical_issues": [], "serious_issues": [],
                         "minor_issues": [], "priority_recommendations": [],
                         "methodology_note": "Badanie przeprowadzono przy użyciu agentów LLM."})


async def _aggregate(stores, site_context=""):
    chat = AsyncMock(return_value=_NARRATIVE)
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        agg = await generate_aggregated_report(stores, PICKUP_INTENT, "https://pepco.pl/",
                                               site_context=site_context)
    prompt = next(c.args[0][0]["content"] for c in chat.call_args_list
                  if c.kwargs.get("model") == "report" and c.kwargs.get("log") is False)
    return agg, prompt


@pytest.mark.asyncio
async def test_the_aggregate_prompt_gets_the_default_a_declared_text_and_the_gaps(tmp_path):
    _, prompt = await _aggregate(_aggregate_run(tmp_path / "undeclared"))
    assert UNDECLARED_SITE_CONTEXT in prompt
    assert GAP_LINE not in prompt

    _, prompt = await _aggregate(_aggregate_run(tmp_path / "declared", "Katalog."))
    assert "Katalog." in prompt and UNDECLARED_SITE_CONTEXT not in prompt

    stores = _aggregate_run(tmp_path / "gap", "Katalog.")
    write_premise_check(tmp_path / "gap", {"version": 1, "premises": {
        "pickup": {"status": "absent", "text": GAP, "evidence": SITE_SAYS}}})
    agg, prompt = await _aggregate(stores)
    assert "Katalog." in prompt and GAP_LINE in prompt
    assert "Oferta w sklepie może się różnić" in prompt
    # The outcome the narrator reads says which part of the task it speaks for.
    assert "2/2 zadań zakładało funkcję, której sesje nie zaobserwowały w serwisie" in prompt
    assert "odbiór zamówienia w sklepie" in prompt
    # ... and the report says it whatever the model wrote.
    assert agg["statistics"]["premise_gaps"][0]["premise"] == "pickup"
    note = resolve_narrative(agg)["methodology_note"]
    assert "to granica zakresu serwisu, nie wada interfejsu" in note and GAP in note
    assert resolve_narrative(agg)["methodology_note"] == note, "not appended twice"
    # The HTML and the Word file print that note, so both carry it.
    html = (tmp_path / "gap" / "aggregated_report.html").read_text(encoding="utf-8")
    assert "granica zakresu serwisu" in html and "nie zaobserwowano: odbiór zamówienia w sklepie" in html
    docx = Document(build_docx(agg, tmp_path / "gap", tmp_path / "r.docx"))
    text = " ".join(para.text for para in docx.paragraphs)
    assert "granica zakresu serwisu" in text and "nie zaobserwowano: odbiór zamówienia w sklepie" in text


@pytest.mark.asyncio
async def test_a_gap_only_the_locator_backs_is_not_told_as_a_fact_about_the_site(tmp_path):
    # No sentence of the site's own, only two sessions that found nothing at the locator: the
    # methodology note of every renderer and the line the passes get say so, and neither
    # forbids reporting a locator that is broken.
    stores = _aggregate_run(tmp_path / "gap", "Katalog.")
    write_premise_check(tmp_path / "gap", {"version": 1, "premises": {
        "pickup": {"status": "absent", "text": GAP, "evidence": []}}})
    agg, prompt = await _aggregate(stores)
    note = resolve_narrative(agg)["methodology_note"]
    assert "sesje nie znalazły w serwisie" in note and "serwis sam tego nie potwierdza" in note
    assert "nie wada interfejsu" not in note and "nie zaobserwowały" not in note and GAP in note
    assert resolve_narrative(agg)["methodology_note"] == note, "not appended twice"
    assert "serwis sam tego nie potwierdza" in prompt and "Nie traktuj jej braku" not in prompt
    html = (tmp_path / "gap" / "aggregated_report.html").read_text(encoding="utf-8")
    assert "serwis sam tego nie potwierdza" in html and "nie wada interfejsu" not in html
    docx = Document(build_docx(agg, tmp_path / "gap", tmp_path / "r.docx"))
    assert "serwis sam tego nie potwierdza" in " ".join(para.text for para in docx.paragraphs)
    # an aggregate stored before the basis was kept is told as the weaker of the two
    agg["statistics"]["premise_gaps"] = [{"premise": "pickup", "label": "x", "text": GAP}]
    assert "serwis sam tego nie potwierdza" in resolve_narrative(agg)["methodology_note"]
    # both kinds at once: each says what it rests on
    agg["statistics"]["premise_gaps"] = [
        {"premise": "pickup", "label": "x", "text": "A.", "basis": "locator"},
        {"premise": "store_stock", "label": "y", "text": "B.", "basis": "site"}]
    note = resolve_narrative(agg)["methodology_note"]
    assert "nie wada interfejsu. B." in note and "stwierdzenie o serwisie. A." in note


@pytest.mark.asyncio
async def test_the_reports_say_what_the_site_was_described_as(tmp_path):
    # A catalog judged as a catalog: the HTML, the PDF's source and the Word file all print
    # the methodology note, so the description the passes were given is in all of them.
    agg, _ = await _aggregate(_aggregate_run(tmp_path / "declared", "Katalog\nbez koszyka."))
    assert agg["statistics"]["site_context"] == "Katalog\nbez koszyka."
    note = resolve_narrative(agg)["methodology_note"]
    assert "opisem jego charakteru podanym w badaniu: „Katalog bez koszyka.”" in note
    assert resolve_narrative(agg)["methodology_note"] == note, "not appended twice"
    html = (tmp_path / "declared" / "aggregated_report.html").read_text(encoding="utf-8")
    assert "Katalog bez koszyka." in html
    docx = Document(build_docx(agg, tmp_path / "declared", tmp_path / "r.docx"))
    assert "Katalog bez koszyka." in " ".join(para.text for para in docx.paragraphs)

    # The description typed in the wizard reaches the aggregate as a parameter and wins.
    agg, _ = await _aggregate(_aggregate_run(tmp_path / "typed", "Sklep."), "Opis z kreatora.")
    assert agg["statistics"]["site_context"] == "Opis z kreatora."

    # Nothing declared: the passes' default is an instruction to them, never printed.
    agg, _ = await _aggregate(_aggregate_run(tmp_path / "undeclared"))
    assert agg["statistics"]["site_context"] == ""
    assert "opisem jego charakteru" not in resolve_narrative(agg)["methodology_note"]
    assert "nie zakładaj" not in resolve_narrative(agg)["methodology_note"]


@pytest.mark.asyncio
async def test_a_reaudit_of_a_finished_run_computes_the_gaps_itself(tmp_path):
    # No premise_check.json: the aggregate makes one (nothing here to find, so no gap).
    stores = _aggregate_run(tmp_path)
    agg, prompt = await _aggregate(stores)
    assert (tmp_path / "premise_check.json").is_file()
    assert agg["statistics"]["premise_gaps"] == []
    assert "zakładało funkcję" not in prompt
