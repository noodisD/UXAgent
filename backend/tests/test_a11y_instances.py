"""Each accessibility occurrence keeps its own page, device and wording.

The 2026-09-27 hyundai run merged one barrier topic across pages: one
description (the test-drive combobox, the worst), one flat list of every page
and one of every participant. The aggregate model paired them and wrote about
comboboxes "on the home page", where the home page had an unnamed button on
mobile only."""
import json
from pathlib import Path

from simulated_web_agent.main.reporter import _a11y_for_prompt, aggregate_statistics
from simulated_web_agent.main.trace_store import TraceStore

FORM = "https://www.hyundai.com/pl/pl/jazda-testowa.html?select-model-name=G4%7CS6"
HOME = "https://www.hyundai.com/pl/pl"


def _session(tmp_path: Path, name: str, device: str, persona: str, finding: dict):
    store = TraceStore(tmp_path / name)
    store.trace_dir.mkdir(parents=True, exist_ok=True)
    store.write_persona_card({"device": device})
    report = {
        "trace_dir": str(store.trace_dir), "persona": f"Persona: {persona}",
        "first_impression": {}, "heuristic_findings": [],
        "accessibility": {"overall_score": 3, "findings": [finding]},
        "survey_result": {"answers": []},
    }
    return store, report


def _finding(url, detail, severity):
    return {"category": "Brak etykiety dla kontrolki", "severity": severity, "detail": detail,
            "recommendation": "Nadać nazwę.", "url": url, "step": 1, "verified": True}


def _stats(tmp_path):
    a, ra = _session(tmp_path, "s_desktop", "desktop", "Magdalena", _finding(
        FORM, "Na stronie formularza 1 pole wyboru (combobox) bez nazwy.", "krytyczny"))
    b, rb = _session(tmp_path, "s_mobile", "mobile", "Krzysztof", _finding(
        HOME, "Na stronie głównej 1 przycisk bez nazwy.", "poważny"))
    return aggregate_statistics([ra, rb], stores=[a, b])["accessibility"]


def test_every_occurrence_carries_its_page_device_and_wording(tmp_path):
    acc = _stats(tmp_path)
    instances = {i["persona"]: i for i in acc["findings"][0]["instances"]}
    assert instances["Magdalena"]["device"] == "desktop"
    assert instances["Magdalena"]["page"].endswith("/pl/pl/jazda-testowa.html")
    assert "combobox" in instances["Magdalena"]["detail"]
    assert instances["Krzysztof"]["device"] == "mobile"
    assert instances["Krzysztof"]["page"] == HOME
    assert "przycisk" in instances["Krzysztof"]["detail"]


def test_the_model_sees_instances_not_the_lists_that_can_be_re_paired(tmp_path):
    view = _a11y_for_prompt(_stats(tmp_path))
    finding = view["findings"][0]
    assert sorted(finding) == ["category", "instances", "recommendation", "severity",
                               "topic", "verified"]
    text = json.dumps(view, ensure_ascii=False)
    assert "urls" not in text and "personas" not in text and "examples" not in text


def test_the_stored_entry_still_has_what_the_renderers_read(tmp_path):
    finding = _stats(tmp_path)["findings"][0]
    for key in ("urls", "personas", "examples", "other_instances", "detail", "verified"):
        assert key in finding, key


def test_sessions_without_a_recorded_device_leave_it_empty(tmp_path):
    store, report = _session(tmp_path, "old", "", "Ola", _finding(HOME, "1 przycisk bez nazwy.", "drobny"))
    acc = aggregate_statistics([report], stores=[store])["accessibility"]
    assert acc["findings"][0]["instances"][0]["device"] == ""


def test_the_aggregate_prompt_tells_the_model_to_keep_instances_apart():
    prompt = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
              / "audit_prompts" / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert "instances" in prompt and "Nigdy nie łącz opisu jednej instancji" in prompt
