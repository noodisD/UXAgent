"""Helpers that are tested alone must also reach what a reader or the model sees.

A review mutated production code so that a helper's output was swapped for "x" or
for the raw data, and the whole suite stayed green: the helpers had tests, the
places that call them had none."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main.reporter import (
    _a11y_for_prompt, aggregate_statistics, generate_aggregated_report,
)
from simulated_web_agent.main.trace_store import TraceStore

NARRATIVE = {"executive_summary": "S.", "overall_ux_score": 3, "key_strengths": [],
             "critical_issues": [], "serious_issues": [], "minor_issues": [],
             "priority_recommendations": [], "first_impression_assessment": "OK",
             "accessibility_assessment": "OK", "responsiveness_assessment": "OK",
             "methodology_note": "T"}

FORM = "https://www.hyundai.com/pl/pl/jazda-testowa.html"
HOME = "https://www.hyundai.com/pl/pl"


def _session(tmp_path: Path, name: str, **report):
    store = TraceStore(tmp_path / name)
    store.trace_dir.mkdir(parents=True, exist_ok=True)
    base = {"trace_dir": str(store.trace_dir), "persona": f"Persona: {name}",
            "first_impression": {}, "heuristic_findings": [],
            "accessibility": {"overall_score": 3, "findings": []},
            "survey_result": {"answers": []}}
    base.update(report)
    store.write_ux_report(base)
    return store


# --- what the aggregate model is sent --------------------------------------------

@pytest.mark.asyncio
async def test_the_aggregate_prompt_carries_instances_and_this_studys_legend(tmp_path):
    finding = {"category": "Brak etykiety dla kontrolki", "severity": "krytyczny",
               "detail": "Na stronie formularza 1 pole wyboru bez nazwy.", "url": FORM,
               "step": 1, "verified": True, "recommendation": "Nadać nazwę."}
    stores = [_session(tmp_path, "Magdalena",
                       accessibility={"overall_score": 3, "findings": [finding]},
                       survey_result={"answers": [{"question_id": "g1", "value": "4"}]}),
              _session(tmp_path, "Krzysztof",
                       accessibility={"overall_score": 3, "findings": [dict(finding, url=HOME)]},
                       survey_result={"answers": [{"question_id": "g1", "value": "5"}]})]
    (tmp_path / "run_manifest.json").write_text(json.dumps({"questionnaire": {"questions": [
        {"id": "g1", "kind": "scale", "prompt": "Strona była łatwa w obsłudze.", "min": 1, "max": 5},
    ]}}), encoding="utf-8")
    chat = AsyncMock(return_value=json.dumps(NARRATIVE))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        result = await generate_aggregated_report(
            stores=stores, task_description="t", website_url="https://www.hyundai.com")
    # The survey summary is a second call; the aggregate one is the model="report" call.
    aggregate = [c for c in chat.call_args_list if c.kwargs.get("model") == "report"]
    assert len(aggregate) == 1, [c.kwargs for c in chat.call_args_list]
    prompt = aggregate[0].args[0][0]["content"]
    acc = result["statistics"]["accessibility"]
    sent = json.dumps(_a11y_for_prompt(acc), ensure_ascii=False, indent=2)
    raw = json.dumps(acc, ensure_ascii=False, indent=2)
    assert sent in prompt, "the accessibility block the model reads is the instances view"
    assert raw not in prompt and '"urls"' not in sent and '"personas"' not in sent
    assert "- g1 = Strona była łatwa w obsłudze. (skala 1–5)" in prompt, "the legend comes from the study"


# --- what a held finding says, as aggregate_statistics hands it to the renderers --------

def _held(tmp_path, **finding):
    store = _session(tmp_path, "Ala")
    f = {"heuristic_id": 6, "severity": "drobny", "step": 4, "verified": False,
         "evidence": "Przycisk jest nieczytelny.", "recommendation": "Poprawić.",
         "harness_suspect": True, **finding}
    report = {"trace_dir": str(store.trace_dir), "persona": "Persona: Ala", "first_impression": {},
              "heuristic_findings": [f], "accessibility": {"findings": []},
              "survey_result": {"answers": []}}
    return aggregate_statistics([report], stores=[store])["heuristic"]["needs_verification_items"]


def test_the_held_item_carries_the_reason_and_the_check_of_its_cause(tmp_path):
    (item,) = _held(tmp_path, harness_cause={"code": "consent", "detail": "", "step": 4})
    assert "baner zgody" in item["reason"]
    assert "po zamknięciu banera zgody" in item["check"]
    assert item["reason"] != "x" and item["check"] != "x"


def test_a_different_cause_gives_a_different_sentence(tmp_path):
    (consent,) = _held(tmp_path, harness_cause={"code": "consent", "detail": "", "step": 4})
    (wall,) = _held(tmp_path / "w", harness_cause={"code": "bot_wall", "detail": "", "step": 4})
    assert consent["reason"] != wall["reason"] and consent["check"] != wall["check"]
    assert "antybotowej" in wall["reason"]


def test_a_verifier_reason_loses_the_tools_own_vocabulary_before_a_client_reads_it(tmp_path):
    (item,) = _held(tmp_path, harness_suspect=False, claim_verdict="unsupported",
                    claim_verdict_reason=("Zapis sesji: last_action_error: Error executing action: "
                                          "Locator.click: Timeout 5000ms exceeded — tekstu nie ma."))
    shown = item["verifier_reason"]
    assert "last_action_error" not in shown and "Locator.click" not in shown
    assert "tekstu nie ma" in shown, "the rest of the sentence survives"


# --- the Word file: pictures and captions of held items and of the first impression ----------

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")


def test_the_word_file_embeds_the_held_items_screenshot_and_formats_the_first_impression(tmp_path):
    from docx import Document

    from simulated_web_agent.main.report_docx import build_docx

    shots = tmp_path / "p1" / "screenshot"
    shots.mkdir(parents=True)
    (shots / "screenshot_16.png").write_bytes(PNG)
    (shots / "landing.png").write_bytes(PNG)
    agg = {"website": "x.pl", "task": "t",
           "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                         "serious_issues": [], "minor_issues": [], "priority_recommendations": []},
           "statistics": {
               "participant_count": 1,
               "heuristic": {"needs_verification_items": [{
                   "heuristic_id": 6, "heuristic_name": "Rozpoznawanie", "severity_claimed": "drobny",
                   "persona_name": "Magdalena", "step": 16, "evidence": "Etykieta jest po angielsku",
                   "reason": "r", "check": "c", "screenshot": "p1/screenshot/screenshot_16.png"}]},
               "first_impression": {"avg_score": 3.0, "screenshots": [
                   {"persona": "Kacper", "score": 3.0, "screenshot": "p1/screenshot/landing.png"}]}}}
    doc = Document(build_docx(agg, tmp_path, tmp_path / "r.docx"))
    text = " | ".join(p.text for p in doc.paragraphs)
    assert len(doc.inline_shapes) == 3, "the logo, one picture for the held item, one for the first impression"
    assert "krok 16" in text, "the held item's caption"
    assert "Kacper · 3,00/5" in text and "3.0/5" not in text
