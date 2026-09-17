"""The first external review's verdict was that the report is not something you
would send a client: scores with no explanation, an accessibility section that
is one bare number, "H1" with no key anywhere, and the same document whatever
the objective was.

Almost none of that needed new analysis. The rationale per dimension, the
per-persona objective reflection, the accessibility findings and the per-device
measurements were all being computed and then dropped before rendering. These
tests pin that the data now survives aggregation and reaches the page.
"""
import pytest

from simulated_web_agent.main.reporter import aggregate_statistics
from simulated_web_agent.main import reporter_html


def _report(**over):
    base = {
        "trace_dir": "runs/R1/p1",
        "persona": "Persona: Marta\nWiek: 24",
        "intent": "Kup lampkę",
        "first_impression": {
            "overall_score": 3.0,
            "scores": {"purpose_clarity": 2, "navigation_visibility": 4},
            "score_rationale": {
                "purpose_clarity": "Nie widać, czym serwis się zajmuje — hero to sama promocja.",
            },
            "weaknesses": ["Brak jasnego CTA"],
            "strengths": ["Czytelna typografia"],
        },
        "heuristic_findings": [],
        "accessibility": {
            "overall_score": 3,
            "findings": [{
                "category": "Kontrast tekstu", "severity": "poważny",
                "detail": "Bannery mają kontrast 2.9:1.",
                "recommendation": "Podnieść kontrast do 4.5:1.",
                "url": "https://x.pl/",
            }],
        },
        "viewport_findings": [{
            "viewport": "mobile", "horizontal_overflow": True,
            "tap_targets": {"below_24px": 7},
            "contrast": {"below_threshold": 3},
            "small_fonts": [{"size": "11px", "text": "regulamin"}],
            "wide_elements": ["div#hero (1440px)"],
        }],
        "survey_result": {"answers": []},
    }
    base.update(over)
    return base


# --- the data survives aggregation ----------------------------------------

def test_score_rationale_survives():
    """The answer to "what lowered this score?" — generated per persona and
    previously discarded at aggregation."""
    fi = aggregate_statistics([_report()])["first_impression"]
    reasons = fi["dimension_reasons"]["purpose_clarity"]
    assert reasons[0]["persona"] == "Marta"
    assert reasons[0]["score"] == 2
    assert "hero" in reasons[0]["why"]


def test_weaknesses_and_strengths_survive():
    fi = aggregate_statistics([_report()])["first_impression"]
    assert fi["weaknesses"][0]["text"] == "Brak jasnego CTA"
    assert fi["strengths"][0]["persona"] == "Marta"


def test_accessibility_findings_survive_with_their_detail():
    acc = aggregate_statistics([_report()])["accessibility"]
    assert acc["avg_score"] == 3
    f = acc["findings"][0]
    assert f["category"] == "Kontrast tekstu"
    assert f["recommendation"].startswith("Podnieść")
    assert f["urls"] == ["https://x.pl/"]


def test_the_same_barrier_on_many_pages_is_one_entry():
    """Otherwise one repeated problem buries everything else."""
    r2 = _report(trace_dir="runs/R1/p2", persona="Persona: Jan")
    r2["accessibility"]["findings"][0]["url"] = "https://x.pl/b"
    acc = aggregate_statistics([_report(), r2])["accessibility"]
    assert len(acc["findings"]) == 1
    assert sorted(acc["findings"][0]["personas"]) == ["Jan", "Marta"]
    assert len(acc["findings"][0]["urls"]) == 2


def test_worst_severity_wins_when_a_category_repeats():
    r2 = _report(trace_dir="runs/R1/p2", persona="Persona: Jan")
    r2["accessibility"]["findings"][0]["severity"] = "krytyczny"
    acc = aggregate_statistics([_report(), r2])["accessibility"]
    assert acc["findings"][0]["severity"] == "krytyczny"


def test_per_device_measurements_survive():
    """tap_targets / contrast / small_fonts were read by nothing at all."""
    dev = aggregate_statistics([_report()])["viewport"]["devices"][0]
    assert dev["viewport"] == "mobile"
    assert dev["tap_targets_below_min"] == 7
    assert dev["contrast_failures"] == 3
    assert dev["pages_with_overflow"] == 1


def test_a_scan_that_errored_is_not_counted_as_a_measurement():
    r = _report()
    r["viewport_findings"] = [{"viewport": "mobile", "error": "timeout"}]
    assert aggregate_statistics([r])["viewport"]["devices"] == []


# --- and reaches the page --------------------------------------------------

def _html(stats, narrative=None):
    return reporter_html.generate_html_report({
        "statistics": stats,
        "narrative": narrative or {"executive_summary": "x", "overall_ux_score": 3.0},
        "website": "x.pl", "task": "Kup lampkę",
    })


def test_every_heuristic_appears_in_the_legend_with_a_verdict():
    """Issues render as "H1" with no key; a reader cannot tell what was assessed
    or how many of the ten passed."""
    stats = aggregate_statistics([_report()])
    stats["heuristic"]["by_heuristic"] = [{
        "heuristic_id": 3, "worst_severity": "poważny", "sessions_affected": "1 z 1",
    }]
    html = _html(stats)
    for n in range(1, 11):
        assert f">H{n}<" in html, f"H{n} missing from the legend"
    assert "nie stwierdzono naruszeń" in html, "passing heuristics must be stated"
    assert "Nielsena" in html


def test_the_objective_and_its_outcome_lead_the_report():
    stats = aggregate_statistics([_report()])
    stats["objective_reflections"] = [{
        "persona": "Marta", "objective": "Kup lampkę", "outcome": "failed",
        "summary": "Nie udało się dokończyć zakupu.",
        "blockers": ["Koszyk gubił produkt"], "wins": ["Wyszukiwarka działała"],
        "priority_fix": "Naprawić koszyk.",
    }]
    html = _html(stats)
    assert "Realizacja zadania" in html
    assert "Cel nieosiągnięty" in html
    assert "Koszyk gubił produkt" in html
    assert "Wyszukiwarka działała" in html, "what worked must be shown, not only faults"
    # the outcome must precede the heuristic detail
    assert html.index("Realizacja zadania") < html.index("Heurystyki Nielsena")


def test_score_rationale_is_rendered_next_to_the_scores():
    html = _html(aggregate_statistics([_report()]))
    assert "Dlaczego takie oceny" in html
    assert "hero to sama promocja" in html


def test_accessibility_and_device_detail_are_rendered():
    html = _html(aggregate_statistics([_report()]))
    assert "Kontrast tekstu" in html and "Podnieść kontrast" in html
    assert "Zachowanie na urządzeniach" in html
    assert "WCAG 2.5.8" in html
    # The caveat must separate what was measured from what was walked. It no
    # longer names desktop unconditionally — the run chooses the device now, so
    # this fixture (which records none) must not claim one.
    assert "Pomiar techniczny" in html
    assert "nie przebieg ścieżki użytkownika" in html
    assert "desktop" not in html.lower(),         "a run with no recorded device must not be described as a desktop run"


def test_executive_summary_is_not_called_wykonawcze():
    assert "wykonawcze" not in _html(aggregate_statistics([_report()]))


def test_sections_are_omitted_rather_than_rendered_empty():
    """A run with none of this data must not grow empty headings."""
    html = _html(aggregate_statistics([_report(
        first_impression={}, accessibility={}, viewport_findings=[],
    )]))
    for absent in ["Realizacja zadania", "Dlaczego takie oceny",
                   "Bariery dostępności", "Zachowanie na urządzeniach"]:
        assert absent not in html
