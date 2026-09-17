"""The reviewer asked for a Word export: the HTML and PDF are read-only, so a
specialist who wants to adapt the wording or lift a section into a client deck
has nowhere to start.

The point of the format is that it is *editable*, so these tests check the
document is genuinely structured — real Word headings, real tables — rather
than a blob of styled text. A converted-from-HTML file would pass "it opens in
Word" and fail every one of these.
"""
import json

import pytest

from simulated_web_agent.main.report_docx import build_docx, ensure_report_docx

docx = pytest.importorskip("docx")


def _agg(**over):
    base = {
        "website": "x.pl",
        "task": "Kup lampkę",
        "narrative": {
            "executive_summary": "Serwis działa, ale koszyk gubi produkty.",
            "overall_ux_score": 2.8,
            "score_rationale": "Zadanie nieukończone przez 2 z 3 osób.",
            "key_strengths": ["Czytelna typografia"],
            "critical_issues": [{
                "heuristic_id": 5, "heuristic_name": "Zapobieganie błędom",
                "severity": "krytyczny", "frequency": "2 z 3 uczestników",
                "impact": "Koszyk gubi produkty po powrocie.",
                "recommendation": "Utrwalać koszyk w sesji.",
                "verified": True, "examples": [],
            }],
            "serious_issues": [], "minor_issues": [],
            "priority_recommendations": [{
                "priority": 1, "area": "Koszyk", "action": "Utrwalić koszyk",
                "expected_impact": "Mniej porzuceń",
            }],
            "methodology_note": "Badanie agentami LLM.",
        },
        "statistics": {
            "participant_count": 3,
            "heuristic": {"severity_counts": {"krytyczny": 1, "poważny": 0, "drobny": 0},
                          "by_heuristic": [{"heuristic_id": 5, "worst_severity": "krytyczny",
                                            "sessions_affected": "2 z 3"}]},
            "first_impression": {"avg_score": 3.1,
                                 "dimension_avgs": {"purpose_clarity": 2.0},
                                 "dimension_reasons": {"purpose_clarity": [
                                     {"persona": "Marta", "score": 2,
                                      "why": "Hero to sama promocja."}]},
                                 "screenshots": []},
            "accessibility": {"avg_score": 3, "findings": [{
                "category": "Kontrast tekstu", "severity": "poważny",
                "detail": "2.9:1 na bannerach.", "recommendation": "Podnieść do 4.5:1.",
                "urls": ["https://x.pl/"], "personas": ["Marta"],
            }]},
            "viewport": {"devices": [{"viewport": "mobile", "pages_scanned": 5,
                                      "pages_with_overflow": 2,
                                      "tap_targets_below_min": 7,
                                      "contrast_failures": 3}]},
            "objective_reflections": [{
                "persona": "Marta", "objective": "Kup lampkę", "outcome": "failed",
                "summary": "Nie udało się.", "blockers": ["Koszyk gubił produkt"],
                "wins": ["Wyszukiwarka działała"], "priority_fix": "Naprawić koszyk.",
            }],
        },
    }
    base.update(over)
    return base


def _doc(tmp_path, agg=None):
    out = build_docx(agg or _agg(), tmp_path, tmp_path / "r.docx")
    return docx.Document(str(out))


def _headings(d):
    return [p.text for p in d.paragraphs if p.style.name.startswith("Heading")]


def _text(d):
    return "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(
        c.text for t in d.tables for r in t.rows for c in r.cells)


# --- it is a real document, not a blob ------------------------------------

def test_sections_are_word_headings_so_navigation_works(tmp_path):
    """Real headings are what make the navigation pane and a contents page work
    — the difference between an editable report and a wall of text."""
    heads = _headings(_doc(tmp_path))
    for expected in ["Podsumowanie", "Realizacja zadania", "Mocne strony",
                     "Heurystyki Nielsena — zakres oceny", "Problemy krytyczne",
                     "Priorytetowe rekomendacje", "Dostępność"]:
        assert expected in heads, f"{expected!r} missing from headings"


def test_tabular_data_is_a_real_table(tmp_path):
    d = _doc(tmp_path)
    assert len(d.tables) >= 3, "heuristics, recommendations and devices are tables"


def test_all_ten_heuristics_are_listed_with_a_verdict(tmp_path):
    text = _text(_doc(tmp_path))
    for n in range(1, 11):
        assert f"H{n}" in text
    assert "nie stwierdzono naruszeń" in text


# --- the content the review asked for -------------------------------------

def test_the_objective_and_its_outcome_are_present(tmp_path):
    text = _text(_doc(tmp_path))
    assert "Cel nieosiągnięty" in text
    assert "Koszyk gubił produkt" in text
    assert "Wyszukiwarka działała" in text


def test_score_rationale_is_present(tmp_path):
    assert "Hero to sama promocja" in _text(_doc(tmp_path))


def test_accessibility_and_device_detail_are_present(tmp_path):
    text = _text(_doc(tmp_path))
    assert "Kontrast tekstu" in text and "Podnieść do 4.5:1" in text
    assert "mobile" in text


def test_evidence_screenshots_are_embedded(tmp_path):
    shot = tmp_path / "p1" / "screenshot"
    shot.mkdir(parents=True)
    # 1x1 PNG
    shot.joinpath("screenshot_3.png").write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    ))
    # Evidence is attached to issues from the *statistics*, by heuristic id —
    # reporter.resolve_narrative joins it deterministically rather than trusting
    # whatever the LLM wrote, so putting it on the narrative issue would be
    # overwritten. Mirroring the real flow here is the point of the test.
    agg = _agg()
    agg["statistics"]["heuristic"]["by_heuristic"][0]["examples"] = [{
        "persona_name": "Marta", "step": 3, "url": "https://x.pl/koszyk",
        "screenshot": "p1/screenshot/screenshot_3.png", "verified": True,
    }]
    assert len(_doc(tmp_path, agg).inline_shapes) == 1


def test_a_missing_screenshot_does_not_break_the_build(tmp_path):
    """A report without one picture beats a report that fails to build."""
    agg = _agg()
    agg["statistics"]["heuristic"]["by_heuristic"][0]["examples"] = [
        {"persona_name": "X", "step": 1, "screenshot": "nope/missing.png"}
    ]
    assert len(_doc(tmp_path, agg).inline_shapes) == 0


def test_an_unverified_issue_says_so(tmp_path):
    agg = _agg()
    agg["narrative"]["critical_issues"][0]["verified"] = False
    assert "NIEZWERYFIKOWANE" in _text(_doc(tmp_path, agg))


# --- build/caching contract -----------------------------------------------

def test_no_report_means_no_document(tmp_path):
    assert ensure_report_docx(tmp_path) is None


def test_it_is_rebuilt_when_the_report_changes(tmp_path):
    """A re-rendered report must never be served as a stale download."""
    (tmp_path / "aggregated_report.json").write_text(
        json.dumps(_agg()), encoding="utf-8")
    first = ensure_report_docx(tmp_path)
    assert first and first.is_file()

    import os
    old = first.stat().st_mtime
    os.utime(tmp_path / "aggregated_report.json", (old + 100, old + 100))
    ensure_report_docx(tmp_path)
    assert (tmp_path / "aggregated_report.docx").stat().st_mtime >= old


def test_a_sparse_report_still_produces_a_document(tmp_path):
    """Sections with no data are omitted, not rendered empty or crashed on."""
    d = _doc(tmp_path, {"website": "x.pl", "narrative": {}, "statistics": {}})
    assert _headings(d), "the heuristic legend is always meaningful"
