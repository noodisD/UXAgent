"""Heuristic findings, split by the device the participant used.

Karolina asked whether the analysis could be divided into mobile and desktop, to show
what works on one and not the other. The measured layer (contrast, tap targets,
overflow) always was; the heuristic findings were not, because nothing recorded the
device on them. In the 2026-10-01 run all four heuristic issues came from the two
mobile participants and the two desktop participants raised none - a fact the report
could not state.

Each participant uses ONE device, so "0 of 2 on desktop" means those two did not meet
the problem, never that desktop is fine. The wording says so."""
import json
from pathlib import Path

import pytest
from docx import Document

from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter import (
    _attach_issue_evidence, aggregate_statistics, device_split,
)
from simulated_web_agent.main.reporter_html import DEVICES_NOTE, devices_text, generate_html_report
from simulated_web_agent.main.trace_store import TraceStore
from tests.conftest import make_agent_report, make_finding


# --- the arithmetic ---------------------------------------------------------------------

def test_a_mixed_run_counts_participants_per_device_in_a_fixed_order():
    got = device_split({"a", "c"}, {"a": "mobile", "b": "desktop", "c": "mobile", "d": "tablet"},
                       {"mobile": 2, "desktop": 1, "tablet": 1})
    assert got == {"desktop": {"affected": 0, "of": 1}, "mobile": {"affected": 2, "of": 2},
                   "tablet": {"affected": 0, "of": 1}}
    assert list(got) == ["desktop", "mobile", "tablet"]


@pytest.mark.parametrize("sessions", [{}, {"desktop": 3}, {"mobile": 4}, {"desktop": 0, "mobile": 2}])
def test_one_device_or_none_recorded_has_nothing_to_split(sessions):
    assert device_split({"a"}, {"a": "desktop"}, sessions) == {}


# --- through aggregation ----------------------------------------------------------------------

def _run(tmp_path, spec, findings_by, recorded=True):
    """spec: {name: device}; findings_by: {name: [finding, ...]}"""
    stores, reports = [], []
    for name, device in spec.items():
        s = TraceStore(tmp_path / name)
        s.trace_dir.mkdir(parents=True, exist_ok=True)
        if recorded:
            s.write_persona_card({"device": device})
        r = make_agent_report(findings=findings_by.get(name, []))
        r["persona"] = f"Persona: {name}"
        r["trace_dir"] = str(s.trace_dir)
        stores.append(s)
        reports.append(r)
    return aggregate_statistics(reports, stores=stores), stores


def _f(h, step=1, text="Opis problemu"):
    return {**make_finding(heuristic_id=h, severity="drobny", step=step), "evidence": text}


SPEC = {"A": "desktop", "B": "desktop", "C": "mobile", "D": "mobile"}


def test_each_group_says_how_many_on_each_device_met_it(tmp_path):
    stats, _ = _run(tmp_path, SPEC, {"C": [_f(7)], "D": [_f(7), _f(3)]})
    groups = {g["heuristic_id"]: g for g in stats["heuristic"]["by_heuristic"]}
    assert groups[7]["devices"] == {"desktop": {"affected": 0, "of": 2}, "mobile": {"affected": 2, "of": 2}}
    assert groups[3]["devices"] == {"desktop": {"affected": 0, "of": 2}, "mobile": {"affected": 1, "of": 2}}


def test_every_example_knows_its_device(tmp_path):
    stats, _ = _run(tmp_path, SPEC, {"A": [_f(7)], "C": [_f(7)]})
    (group,) = stats["heuristic"]["by_heuristic"]
    assert sorted(e["device"] for e in group["examples"]) == ["desktop", "mobile"]


def test_a_run_on_one_device_or_recorded_before_devices_existed_is_unchanged(tmp_path):
    one, _ = _run(tmp_path / "one", {"A": "desktop", "B": "desktop"}, {"A": [_f(7)]})
    assert one["heuristic"]["by_heuristic"][0]["devices"] == {}
    old, _ = _run(tmp_path / "old", SPEC, {"A": [_f(7)]}, recorded=False)
    group = old["heuristic"]["by_heuristic"][0]
    assert group["devices"] == {} and group["examples"][0]["device"] == ""


def test_a_store_outside_the_report_is_not_counted_in_the_denominator(tmp_path):
    stats, stores = _run(tmp_path, SPEC, {"C": [_f(7)]})
    extra = TraceStore(tmp_path / "Z")
    extra.trace_dir.mkdir(parents=True, exist_ok=True)
    extra.write_persona_card({"device": "mobile"})
    reports = [make_agent_report(findings=[_f(7)]) | {"persona": "Persona: C", "trace_dir": str(stores[2].trace_dir)},
               make_agent_report() | {"persona": "Persona: A", "trace_dir": str(stores[0].trace_dir)}]
    again = aggregate_statistics(reports, stores=stores + [extra])
    assert again["heuristic"]["by_heuristic"][0]["devices"] == {
        "desktop": {"affected": 0, "of": 1}, "mobile": {"affected": 1, "of": 1}}


def test_the_accessibility_instances_still_carry_their_device(tmp_path):
    """The lookup moved ahead of the heuristic groups; the older consumer must not notice."""
    from tests.test_a11y_instances import _finding, _session
    a, ra = _session(tmp_path, "s_d", "desktop", "Ala", _finding("https://x.pl/a", "Brak nazwy przycisku", "poważny"))
    b, rb = _session(tmp_path, "s_m", "mobile", "Basia", _finding("https://x.pl/b", "Brak nazwy przycisku", "poważny"))
    acc = aggregate_statistics([ra, rb], stores=[a, b])["accessibility"]
    assert {i["device"] for i in acc["findings"][0]["instances"]} == {"desktop", "mobile"}


# --- the issue and its rendering -------------------------------------------------------------------

DEVICES = {"desktop": {"affected": 0, "of": 2}, "mobile": {"affected": 2, "of": 2}}


def test_an_issue_carries_its_groups_split_and_an_old_group_gives_none():
    narrative = {"critical_issues": [], "serious_issues": [],
                 "minor_issues": [{"heuristic_id": 7, "heuristic_name": "x", "impact": "i"}]}
    _attach_issue_evidence(narrative, {"heuristic": {"by_heuristic": [
        {"heuristic_id": 7, "verified": True, "examples": [], "devices": DEVICES}]}, "accessibility": {}})
    assert narrative["minor_issues"][0]["devices"] == DEVICES
    narrative["minor_issues"][0].pop("devices")
    _attach_issue_evidence(narrative, {"heuristic": {"by_heuristic": [
        {"heuristic_id": 7, "verified": True, "examples": []}]}, "accessibility": {}})
    assert narrative["minor_issues"][0]["devices"] == {}


@pytest.mark.parametrize("devices, expected", [
    (DEVICES, "desktop 0 z 2 · mobile 2 z 2"),
    ({"desktop": {"affected": 1, "of": 3}}, ""),               # one device: nothing to compare
    ({}, ""), (None, ""), ("x", ""), ({"desktop": "x", "mobile": 3}, ""),
    ({"desktop": {"affected": 0, "of": 0}, "mobile": {"affected": 1, "of": 2}}, ""),
])
def test_the_text_is_only_produced_for_a_real_comparison(devices, expected):
    assert devices_text(devices) == expected


def _agg(devices):
    return {"website": "x.pl", "task": "t",
            "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                          "serious_issues": [],
                          "minor_issues": [{"heuristic_id": 7, "heuristic_name": "Elastyczność",
                                            "frequency": "2 z 4", "impact": "Brak skrótów.",
                                            "recommendation": "Dodać.", "devices": devices}]},
            "statistics": {"participant_count": 4, "heuristic": {"by_heuristic": [
                {"heuristic_id": 7, "worst_severity": "drobny", "sessions_affected": "2 z 4 uczestników",
                 "devices": devices, "examples": [], "verified": True}]}}}


def test_the_html_card_the_legend_and_one_caveat_say_it():
    html = generate_html_report(_agg(DEVICES))
    assert "Na urządzeniach: desktop 0 z 2 · mobile 2 z 2" in html
    assert html.count(DEVICES_NOTE) == 1 and "Problemy a urządzenia" in html
    legend = html.split('<table class="hl-table">')[1].split("</table>")[0]
    assert "2 z 4 uczestników" in legend and "desktop 0 z 2 · mobile 2 z 2" in legend, "the legend row"
    assert "nie dowodzi, że na tym urządzeniu go nie ma" in html


def test_without_a_split_neither_the_line_nor_the_caveat_appears():
    html = generate_html_report(_agg({}))
    assert "Na urządzeniach" not in html and "Problemy a urządzenia" not in html


def test_the_word_file_says_the_same(tmp_path):
    text = " | ".join(p.text for p in Document(build_docx(_agg(DEVICES), tmp_path, tmp_path / "r.docx")).paragraphs)
    assert "na urządzeniach: desktop 0 z 2 · mobile 2 z 2" in text
    assert DEVICES_NOTE in text
    legend = next(t for t in Document(tmp_path / "r.docx").tables if t.rows[0].cells[1].text == "Heurystyka")
    assert any("desktop 0 z 2 · mobile 2 z 2" in row.cells[2].text for row in legend.rows)
    plain = " | ".join(p.text for p in Document(build_docx(_agg({}), tmp_path, tmp_path / "p.docx")).paragraphs)
    assert "na urządzeniach" not in plain and DEVICES_NOTE not in plain


def test_the_aggregate_prompt_explains_the_split_and_forbids_the_overclaim():
    prompt = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent" / "audit_prompts"
              / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert "`devices`" in prompt and "NIE twierdź, że na drugim go nie ma" in prompt
    assert "Gdy `devices` jest puste" in prompt
