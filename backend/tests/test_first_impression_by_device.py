"""The first impression is reported per device, not as one mean over two different screens.

pepco.pl, device "both": three personas on desktop scored the first screen 3.2, 3.0 and
3.0 (mean 3.07), three on mobile 1.2, 1.4 and 1.8 (mean 1.47). The report printed one
mean, 2.27/5 "negative", and its narrative blamed a huge UOKiK notice - a thin strip on
desktop, a screen-filling box only on mobile. The split was invisible, so the cause (a
mobile-only first-screen problem) was lost.
"""
import json
import re

import pytest
from docx import Document
from unittest.mock import AsyncMock, patch

from simulated_web_agent.agent.prompt_registry import registry as prompts
from simulated_web_agent.main import results_api
from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter import (
    _narrative_from_stats, aggregate_statistics, first_impression_by_device, generate_aggregated_report,
)
from simulated_web_agent.main.reporter_html import (
    FI_DEVICE_FLAG, _render_first_impression, first_impression_device_line, generate_html_report,
    stats_table_rows,
)
from simulated_web_agent.main.trace_store import TraceStore
from tests.conftest import make_agent_report

DESKTOP = [3.2, 3.0, 3.0]
MOBILE = [1.2, 1.4, 1.8]
LINE = "komputer 3,07 (n=3) · telefon 1,47 (n=3)"


def _dims(score):
    return {"purpose_clarity": score, "navigation_visibility": 3, "call_to_action": 3,
            "visual_hierarchy": 3, "trustworthiness": 3}


def _run(tmp_path, spec, recorded=True):
    """spec: [(name, device, first-impression score, purpose_clarity score)] -> (stats, stores, reports)"""
    stores, reports = [], []
    for name, device, score, purpose in spec:
        s = TraceStore(tmp_path / name)
        s.trace_dir.mkdir(parents=True, exist_ok=True)
        if recorded:
            s.write_persona_card({"device": device})
        r = make_agent_report(fi_score=score, fi_dimension_scores=_dims(purpose))
        r["persona"] = f"Persona: {name}"
        r["trace_dir"] = str(s.trace_dir)
        r["first_impression"]["score_rationale"] = {"purpose_clarity": f"Powód {name}"}
        s.write_ux_report(r)
        stores.append(s)
        reports.append(r)
    return aggregate_statistics(reports, stores=stores), stores, reports


PEPCO = ([(f"d{i}", "desktop", sc, 4 - i % 2) for i, sc in enumerate(DESKTOP)]
         + [(f"m{i}", "mobile", sc, 1 + i % 2) for i, sc in enumerate(MOBILE)])


@pytest.fixture
def pepco(tmp_path):
    return _run(tmp_path, PEPCO)


# --- the arithmetic ---------------------------------------------------------------------

def test_the_pepco_run_is_split_into_what_it_blended(pepco):
    fi = pepco[0]["first_impression"]
    assert fi["avg_score"] == 2.27
    assert fi["by_device"]["desktop"]["avg_score"] == 3.07 and fi["by_device"]["desktop"]["n"] == 3
    assert fi["by_device"]["mobile"]["avg_score"] == 1.47 and fi["by_device"]["mobile"]["n"] == 3
    assert list(fi["by_device"]) == ["desktop", "mobile"]
    assert fi["device_gap"] == 1.6 and fi["device_dependent"] is True
    assert fi["weakest_device"] == "mobile"


def test_each_dimension_is_split_too(pepco):
    by = pepco[0]["first_impression"]["by_device"]
    assert by["desktop"]["dimension_avgs"]["purpose_clarity"] == 3.67      # 4, 3, 4
    assert by["mobile"]["dimension_avgs"]["purpose_clarity"] == 1.33       # 1, 2, 1
    assert set(by["mobile"]["dimension_avgs"]) == set(by["desktop"]["dimension_avgs"]) and len(by["mobile"]["dimension_avgs"]) == 5
    assert pepco[0]["first_impression"]["dimension_avgs"]["purpose_clarity"] == 2.5, "the blended one stays"


@pytest.mark.parametrize("mobile, flagged", [(2.0, True), (2.01, False), (1.0, True)])
def test_a_gap_of_a_point_is_flagged_and_a_smaller_one_is_not(tmp_path, mobile, flagged):
    """Measured on the averages as printed, so 1,00 apart reads as flagged."""
    stats, *_ = _run(tmp_path, [("d", "desktop", 3.0, 3), ("m", "mobile", mobile, 3)])
    fi = stats["first_impression"]
    assert fi["device_dependent"] is flagged
    assert ("weakest_device" in fi) is flagged
    assert set(fi["by_device"]) == {"desktop", "mobile"}, "the split is shown whatever the gap"


def test_a_device_on_the_better_side_is_not_named_the_weakest(tmp_path):
    stats, *_ = _run(tmp_path, [("d", "desktop", 1.5, 3), ("m", "mobile", 3.5, 3)])
    assert stats["first_impression"]["weakest_device"] == "desktop"


def test_a_run_on_one_device_adds_nothing(tmp_path):
    stats, *_ = _run(tmp_path, [(f"d{i}", "desktop", s, 3) for i, s in enumerate(DESKTOP)])
    fi = stats["first_impression"]
    assert not {"by_device", "device_gap", "device_dependent", "weakest_device"} & set(fi)
    assert fi["avg_score"] == 3.07


def test_a_run_stored_before_the_device_was_recorded_adds_nothing(tmp_path):
    stats, *_ = _run(tmp_path, PEPCO, recorded=False)
    assert "by_device" not in stats["first_impression"]
    assert stats["first_impression"]["avg_score"] == 2.27


def test_a_device_nobody_scored_leaves_no_split(tmp_path):
    """Three mobile first-impression tests failed: the mean is desktop's alone, and
    a one-sided split would compare it with nothing."""
    stats, stores, reports = _run(tmp_path, PEPCO)
    for r in reports[3:]:
        r["first_impression"] = {"error": "429"}
    fi = aggregate_statistics(reports, stores=stores)["first_impression"]
    assert "by_device" not in fi and fi["scored"] == 3 and fi["avg_score"] == 3.07


def test_the_helper_needs_two_devices_with_scores():
    assert first_impression_by_device({"desktop": [3.0]}, {}) == {}
    assert first_impression_by_device({"desktop": [3.0], "mobile": []}, {}) == {}
    assert first_impression_by_device({}, {}) == {}


def test_a_reason_a_weakness_and_a_screen_say_which_device_they_came_from(tmp_path):
    for s in ("d0", "m0"):
        shot = tmp_path / s / "screenshot" / "screenshot_landing.png"
        shot.parent.mkdir(parents=True)
        shot.write_bytes(b"x")
    stats, *_ = _run(tmp_path, PEPCO)
    fi = stats["first_impression"]
    assert {r["device"] for r in fi["dimension_reasons"]["purpose_clarity"]} == {"desktop", "mobile"}
    assert {w["device"] for w in fi["weaknesses"]} == {"desktop", "mobile"}
    assert {w["device"] for w in fi["strengths"]} == {"desktop", "mobile"}
    assert {s["device"] for s in fi["screenshots"]} == {"desktop", "mobile"}


def test_a_single_device_run_keeps_its_reasons_as_they_were(tmp_path):
    stats, *_ = _run(tmp_path, [(f"d{i}", "desktop", s, 3) for i, s in enumerate(DESKTOP)])
    fi = stats["first_impression"]
    assert all("device" not in r for r in fi["dimension_reasons"]["purpose_clarity"])
    assert all("device" not in w for w in fi["weaknesses"])


# --- the Polish line, in every format ------------------------------------------------------

def test_the_line_names_both_devices_and_flags_a_gap(pepco):
    assert first_impression_device_line(pepco[0]) == f"{LINE} — {FI_DEVICE_FLAG}"
    assert FI_DEVICE_FLAG == "wynik zależy od urządzenia"


def test_a_gap_under_a_point_prints_the_split_without_the_flag(tmp_path):
    stats, *_ = _run(tmp_path, [("d", "desktop", 3.0, 3), ("m", "mobile", 2.5, 3)])
    assert first_impression_device_line(stats) == "komputer 3,00 (n=1) · telefon 2,50 (n=1)"


TWO = {"desktop": {"avg_score": 3.0}, "mobile": {"avg_score": 1.0}}


@pytest.mark.parametrize("stats, expected", [
    ({}, ""), (None, ""), ({"first_impression": None}, ""), ({"first_impression": {}}, ""),
    ({"first_impression": {"avg_score": 2.27, "scored": 6, "of": 6}}, ""),          # stored before the split
    ({"first_impression": {"by_device": {"desktop": {"avg_score": 3.0, "n": 3}}}}, ""),   # one device
    ({"first_impression": {"by_device": "x"}}, ""),
    ({"first_impression": {"by_device": {"desktop": "x", "mobile": 3}}}, ""),
    ({"first_impression": {"by_device": {"desktop": {"avg_score": None}, "mobile": {"avg_score": True}}}}, ""),
    # a count the record lacks is left out rather than printed as "n=None"
    ({"first_impression": {"by_device": TWO}}, "komputer 3,00 · telefon 1,00"),
    # only a real True flags
    ({"first_impression": {"by_device": TWO, "device_dependent": "yes"}}, "komputer 3,00 · telefon 1,00"),
    ({"first_impression": {"by_device": TWO, "device_dependent": True}},
     "komputer 3,00 · telefon 1,00 — wynik zależy od urządzenia"),
])
def test_every_reader_defaults_safely(stats, expected):
    assert first_impression_device_line(stats) == expected


def _agg(stats):
    return {"website": "x.pl", "task": "t", "narrative": {"executive_summary": "S."}, "statistics": stats}


def test_the_html_prints_the_split_beside_the_headline_and_in_the_section(pepco):
    html = generate_html_report(_agg(pepco[0]))
    note = f"Pierwsze wrażenie według urządzenia: {LINE} — {FI_DEVICE_FLAG}."
    assert html.count(note) == 1 and 'class="header-note"' in html.split(note)[0].rsplit("<div", 1)[1]
    assert f"Według urządzenia:</strong> {LINE} — {FI_DEVICE_FLAG}." in html
    assert dict(stats_table_rows(pepco[0]))["Pierwsze wrażenie wg urządzenia"] == f"{LINE} — {FI_DEVICE_FLAG}"
    assert "2,27/5" in html, "the overall mean is still printed, beside the split"


def test_the_html_names_the_device_under_each_first_screen(tmp_path):
    for s in ("d0", "m0"):
        shot = tmp_path / s / "screenshot" / "screenshot_landing.png"
        shot.parent.mkdir(parents=True)
        shot.write_bytes(b"x")
    stats, *_ = _run(tmp_path, PEPCO)
    section = _render_first_impression(stats, {})
    assert re.findall(r"<figcaption>([^<]*)</figcaption>", section) == ["d0 · komputer · 3,20/5", "m0 · telefon · 1,20/5"]


def test_a_single_device_or_an_older_report_renders_as_before(tmp_path):
    one, *_ = _run(tmp_path / "one", [(f"d{i}", "desktop", s, 3) for i, s in enumerate(DESKTOP)])
    old, *_ = _run(tmp_path / "old", PEPCO, recorded=False)
    for stats in (one, old):
        html = generate_html_report(_agg(stats))
        assert "według urządzenia" not in html and "Według urządzenia" not in html
        assert FI_DEVICE_FLAG not in html
        assert "Pierwsze wrażenie wg urządzenia" not in dict(stats_table_rows(stats))


def test_the_html_does_not_flag_a_small_gap(tmp_path):
    stats, *_ = _run(tmp_path, [("d", "desktop", 3.0, 3), ("m", "mobile", 2.5, 3)])
    html = generate_html_report(_agg(stats))
    assert "komputer 3,00 (n=1) · telefon 2,50 (n=1)" in html and FI_DEVICE_FLAG not in html


def test_the_word_file_prints_the_same_line(pepco, tmp_path):
    doc = Document(build_docx(_agg(pepco[0]), tmp_path, tmp_path / "r.docx"))
    paragraphs = [p.text for p in doc.paragraphs]
    assert f"Według urządzenia: {LINE} — {FI_DEVICE_FLAG}" in paragraphs
    assert paragraphs.index("Średnia ocena: 2,27/5") < paragraphs.index(
        f"Według urządzenia: {LINE} — {FI_DEVICE_FLAG}")
    cells = [c.text for t in doc.tables for row in t.rows for c in row.cells]
    assert "Pierwsze wrażenie wg urządzenia" in cells and f"{LINE} — {FI_DEVICE_FLAG}" in cells


def test_the_word_file_of_a_single_device_run_is_unchanged(tmp_path):
    stats, *_ = _run(tmp_path / "run", [(f"d{i}", "desktop", s, 3) for i, s in enumerate(DESKTOP)])
    doc = Document(build_docx(_agg(stats), tmp_path, tmp_path / "r.docx"))
    assert not any("urządzenia" in p.text and "Według" in p.text for p in doc.paragraphs)


# --- the dashboard payload ------------------------------------------------------------------------

def _dashboard_run(tmp_path, stats):
    run_dir = tmp_path / "2026-10-20_10-00-00_test"
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(json.dumps(
        {"run_id": run_dir.name, "site": "x.pl", "status": "done"}), encoding="utf-8")
    (run_dir / "aggregated_report.json").write_text(
        json.dumps({"statistics": stats, "narrative": {}}), encoding="utf-8")
    return run_dir


def test_the_dashboard_payload_carries_the_split(pepco, tmp_path):
    run_dir = _dashboard_run(tmp_path / "runs", pepco[0])
    got = results_api.build_run(run_dir.name, tmp_path / "runs")["firstImpressionByDevice"]
    assert got["text"] == f"{LINE} — {FI_DEVICE_FLAG}"
    assert got["devices"] == {"desktop": {"avg": 3.07, "n": 3}, "mobile": {"avg": 1.47, "n": 3}}
    assert got["gap"] == 1.6 and got["deviceDependent"] is True and got["flag"] == FI_DEVICE_FLAG


def test_the_dashboard_payload_has_nothing_for_one_device_or_an_older_report(tmp_path):
    one, *_ = _run(tmp_path / "one", [(f"d{i}", "desktop", s, 3) for i, s in enumerate(DESKTOP)])
    for i, stats in enumerate((one, {"participant_count": 3, "first_impression": {"avg_score": 2.27}}, {})):
        run_dir = _dashboard_run(tmp_path / f"runs{i}", stats)
        assert results_api.build_run(run_dir.name, tmp_path / f"runs{i}")["firstImpressionByDevice"] is None
    assert results_api._first_impression_by_device(None) is None
    assert results_api._first_impression_by_device({"statistics": "x"}) is None


def test_a_small_gap_reaches_the_dashboard_unflagged(tmp_path):
    stats, *_ = _run(tmp_path / "s", [("d", "desktop", 3.0, 3), ("m", "mobile", 2.5, 3)])
    got = results_api._first_impression_by_device({"statistics": stats})
    assert got["deviceDependent"] is False and got["flag"] is None and got["gap"] == 0.5


# --- the narrator ----------------------------------------------------------------------------------

NARRATIVE = {"executive_summary": "S.", "overall_ux_score": 3, "key_strengths": [], "critical_issues": [],
             "serious_issues": [], "minor_issues": [], "priority_recommendations": [],
             "first_impression_assessment": "OK", "accessibility_assessment": "OK",
             "responsiveness_assessment": "OK", "methodology_note": "T"}


@pytest.mark.asyncio
async def test_the_narrator_is_handed_the_split_and_the_stored_report_keeps_it(tmp_path):
    _, stores, _ = _run(tmp_path, PEPCO)
    chat = AsyncMock(return_value=json.dumps(NARRATIVE))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        agg = await generate_aggregated_report(stores, "Kup coś", "https://x.pl/")
    prompt = next(c.args[0][0]["content"] for c in chat.call_args_list
                  if c.kwargs.get("model") == "report" and c.kwargs.get("log") is False)
    block = prompt.split("FIRST IMPRESSION STATISTICS:")[1].split("WAŻNE (pierwsze wrażenie a urządzenia)")[0]
    payload = json.loads(block[block.index("{"):block.rindex("}") + 1])
    assert payload["by_device"]["desktop"]["avg_score"] == 3.07 and payload["by_device"]["mobile"]["avg_score"] == 1.47
    assert payload["by_device"]["mobile"]["n"] == 3
    assert payload["device_dependent"] is True and payload["weakest_device"] == "mobile"
    assert {r["device"] for r in payload["dimension_reasons"]["purpose_clarity"]} == {"desktop", "mobile"}
    assert agg["statistics"]["first_impression"]["by_device"]["mobile"]["avg_score"] == 1.47


@pytest.mark.asyncio
async def test_a_single_device_run_hands_the_narrator_no_split(tmp_path):
    _, stores, _ = _run(tmp_path, [(f"d{i}", "desktop", s, 3) for i, s in enumerate(DESKTOP)])
    chat = AsyncMock(return_value=json.dumps(NARRATIVE))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        await generate_aggregated_report(stores, "Kup coś", "https://x.pl/")
    prompt = next(c.args[0][0]["content"] for c in chat.call_args_list
                  if c.kwargs.get("model") == "report" and c.kwargs.get("log") is False)
    block = prompt.split("FIRST IMPRESSION STATISTICS:")[1].split("WAŻNE (pierwsze wrażenie a urządzenia)")[0]
    assert '"by_device"' not in block and '"device_dependent"' not in block


def test_the_prompt_formats_and_carries_the_rule():
    template = prompts.get("aggregate_synthesize")
    names = set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", template))
    text = template.format(**{n: f"<{n}>" for n in names})      # a stray brace raises here
    assert "<first_impression_stats>" in text
    rule = text.split("WAŻNE (pierwsze wrażenie a urządzenia)")[1].split("HEURISTIC VIOLATION SUMMARY")[0]
    for must in ("`by_device`", "`device_dependent`", "`weakest_device`", "o 1,0 lub więcej", "MUSI wskazać urządzenie",
                 "NIE wolno w nim przedstawiać ogólnej średniej", "pierwszego wrażenia strony",
                 "nic o urządzeniach"):
        assert must in rule, must
    assert "- first_impression_assessment (urządzenia): przy `device_dependent` równym true" in text, "and once more beside the output rules"


# --- the narrator's stand-in when its call failed ----------------------------------------------------------

def test_the_automatic_summary_does_not_present_the_blend_as_the_first_impression(pepco):
    narr = _narrative_from_stats(pepco[0])
    for text in (narr["executive_summary"], narr["first_impression_assessment"]):
        assert f"Pierwsze wrażenie według urządzenia: {LINE} — {FI_DEVICE_FLAG}." in text
        assert "2.27" not in text and "2,27" not in text


def test_the_automatic_summary_of_a_small_gap_or_one_device_is_as_before(tmp_path):
    small, *_ = _run(tmp_path / "s", [("d", "desktop", 3.0, 3), ("m", "mobile", 2.5, 3)])
    assert "Pierwsze wrażenie oceniono na 2.75/5." in _narrative_from_stats(small)["executive_summary"]
    plain = _narrative_from_stats({"participant_count": 3, "heuristic": {},
                                   "first_impression": {"avg_score": 2.27, "dimension_avgs": {}}})
    assert "Pierwsze wrażenie oceniono na 2.27/5." in plain["executive_summary"]
    assert plain["first_impression_assessment"].startswith("Pierwsze wrażenie: 2.27/5.")
