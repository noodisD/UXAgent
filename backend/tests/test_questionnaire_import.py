"""The wizard's pasted-questionnaire validator and its preset helper.

The frontend has no JS test runner, so the two pure modules are loaded by node
(which strips the TypeScript types itself) and checked from here. survey.py
hands the persona every key of a question except the scoring markers, so a pasted
key the validator drops is a field the persona never sees.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _node(expr: str):
    script = f"""
import {{ parseQuestionnaireJson }} from {json.dumps((SRC / 'questionnaireImport.ts').as_uri())}
import {{ isCustomSiteContext, PRESET_SITE_CONTEXT }} from {json.dumps((SRC / 'surveyPresets.ts').as_uri())}
console.log(JSON.stringify({expr}))
"""
    done = subprocess.run(
        ["node", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def _parse(doc) -> dict:
    text = doc if isinstance(doc, str) else json.dumps(doc)
    return _node(f"parseQuestionnaireJson({json.dumps(text)})")


def test_question_keys_the_persona_reads_survive_the_paste():
    res = _parse({
        "questions": [{
            "id": " q1 ", "type": "multiple_choice", "prompt": " Rate it ",
            "options": ["1", "2", "3", "4", "5"],
            "scale_labels": {"1": "Strongly disagree", "5": "Strongly agree"},
            "sus_item": 3, "reverse": False,
        }],
    })
    assert res["ok"], res
    q = res["questionnaire"]["questions"][0]
    assert q["scale_labels"] == {"1": "Strongly disagree", "5": "Strongly agree"}
    assert (q["id"], q["prompt"], q["sus_item"], q["reverse"]) == ("q1", "Rate it", 3, False)


def test_top_level_keys_survive_and_empty_meta_is_left_out():
    res = _parse({
        "questionnaire_id": "", "title": " Moja ankieta ", "instructions": "Odpowiadaj krótko.",
        "questions": [{"id": "q1", "type": "short_text", "prompt": "Co sądzisz?"}],
    })
    assert res["ok"], res
    out = res["questionnaire"]
    assert out["instructions"] == "Odpowiadaj krótko."
    assert out["title"] == "Moja ankieta"
    assert "questionnaire_id" not in out


def test_null_markers_and_empty_options_do_not_reach_the_questionnaire():
    res = _parse({
        "questions": [{"id": "q1", "type": "short_text", "prompt": "Co sądzisz?",
                       "options": [], "sus_item": None, "reverse": None}],
    })
    assert res["ok"], res
    q = res["questionnaire"]["questions"][0]
    assert q == {"id": "q1", "type": "short_text", "prompt": "Co sądzisz?"}


def test_a_leading_byte_order_mark_is_not_a_json_error():
    doc = json.dumps({"questions": [{"id": "q1", "type": "short_text", "prompt": "Co sądzisz?"}]})
    assert _parse("﻿" + doc)["ok"]


def test_known_fields_are_still_checked():
    bad = _parse({"questions": [{"id": "q1", "type": "multiple_choice", "prompt": "x", "scale_labels": {}}]})
    assert not bad["ok"] and bad["issue"]["key"] == "w2.import.err.noOptions"


def test_only_a_description_somebody_wrote_is_custom():
    texts = _node("Object.values(PRESET_SITE_CONTEXT)")
    assert texts
    for text in texts:
        assert _node(f"isCustomSiteContext({json.dumps('  ' + text + ' ')})") is False
    assert _node("isCustomSiteContext('')") is False
    assert _node("isCustomSiteContext('   ')") is False
    assert _node("isCustomSiteContext('Serwis banku: brak koszyka i zakupów online.')") is True
