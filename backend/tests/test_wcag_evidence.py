"""Accessibility claims must carry their proof and their denominator.

Reviewing the user's hyundai run against the first external review found two
of four client-facing issues were WCAG claims with examples=[] and
verified=false — rendered at full card prominence, one of them critical — while
the per-persona accessibility_findings.json held a step, a screenshot and a
quote for each. Two separate faults produced that:

  * aggregation copied category/severity/detail/recommendation/urls and dropped
    the evidence fields;
  * the evidence join keys on heuristic_id, and WCAG issues carry null, so it
    could never have matched anyway.

And one claim was false outright: "Brak tekstów alternatywnych dla kluczowych
obrazów produktu (samochodu, opcji kolorystycznych, wnętrza)", serious, "13 z 13
stron". The run's snapshots named 1154 of 1287 images, every car by its model.
The candidate checks counted failures and never the total, so the narrator had
no way to tell five icons from every image on the page.
"""
import copy
import json

import pytest

from simulated_web_agent.main.a11y_checks import a11y_topic, measure_names
from simulated_web_agent.main.reporter import (
    _ALT_TEXT_SYSTEMIC_SHARE,
    _attach_issue_evidence,
)
from simulated_web_agent.main.trace_store import TraceStore


# --- the denominator ---------------------------------------------------------

def _snapshots(tmp_path, *pages):
    store = TraceStore(tmp_path)
    store.write_action_trace([json.dumps({"action": "click", "target": "x"})] * len(pages))
    for i, page in enumerate(pages):
        (tmp_path / "a11y_snapshots").mkdir(exist_ok=True)
        (tmp_path / "a11y_snapshots" / f"a11y_snapshot_{i}.json").write_text(
            json.dumps(page), encoding="utf-8")
    return store


def test_measure_names_counts_the_named_as_well_as_the_unnamed(tmp_path):
    page = "\n".join([
        '- img "IONIQ 5"', '- img "KONA Electric"', '- img "TUCSON"', "- img",
        '- button "Dalej"', "- button",
    ])
    m = measure_names(_snapshots(tmp_path, page))
    assert m["images"] == {"total": 4, "unnamed": 1, "share_unnamed": 0.25}
    assert m["controls"]["total"] == 2 and m["controls"]["unnamed"] == 1


def test_no_snapshots_gives_no_share_rather_than_zero(tmp_path):
    """A share of 0.0 would read as "measured, and fine"."""
    m = measure_names(TraceStore(tmp_path))
    assert m["images"]["share_unnamed"] is None


@pytest.mark.parametrize("text,topic", [
    ("Brak tekstów alternatywnych dla kluczowych obrazów produktu", "alt_text"),
    ("Brak tekstu alternatywnego", "alt_text"),
    ("Brak etykiet przycisków w formularzach", "control_name"),
    ("Brakujące etykiety kontrolek", "control_name"),
    ("Niewystarczający kontrast tekstu", "contrast"),
    ("Wydajność strony mobilnej", None),
])
def test_topic_classification(text, topic):
    assert a11y_topic(text) == topic


# --- the join ---------------------------------------------------------------

def _stats(*, share_images=0.1, alt_verified=True, label_verified=True):
    ex = lambda step: [{"persona_dir": "p1", "persona_name": "Anna", "step": step,
                        "url": "https://x.pl/", "screenshot": f"p1/screenshot/screenshot_{step}.png",
                        "observation_quote": "cytat", "evidence": "szczegół", "verified": True}]
    return {
        "heuristic": {"by_heuristic": []},
        "accessibility": {
            "findings": [
                {"category": "Brakujące etykiety kontrolek", "topic": "control_name",
                 "examples": ex(18), "verified": label_verified},
                {"category": "Brak tekstu alternatywnego", "topic": "alt_text",
                 "examples": ex(0), "verified": alt_verified},
            ],
            "measured": {"images": {"total": 1287, "unnamed": int(1287 * share_images),
                                    "share_unnamed": share_images}},
        },
    }


def _wcag(impact):
    return {"heuristic_id": None, "heuristic_name": "Dostępność (WCAG)",
            "impact": impact, "recommendation": "", "frequency": ""}


def test_a_wcag_issue_gets_the_evidence_its_finding_carried():
    narr = {"critical_issues": [_wcag("Brak etykiet przycisków w formularzach")]}
    _attach_issue_evidence(narr, _stats())
    issue = narr["critical_issues"][0]
    assert issue["verified"] is True
    assert issue["examples"][0]["step"] == 18
    assert issue["examples"][0]["screenshot"].endswith("screenshot_18.png")


def test_an_overstated_alt_text_claim_is_demoted_with_the_number_shown():
    """The false claim from the run: serious, while 90% of images were named."""
    narr = {"serious_issues": [_wcag("Brak tekstów alternatywnych dla obrazów produktu")]}
    _attach_issue_evidence(narr, _stats(share_images=0.103))
    assert narr["serious_issues"] == []
    moved = narr["minor_issues"][0]
    assert moved["demoted_from"] == "serious_issues"
    assert moved["demotion_reason"] == "measured_share"
    assert moved["severity"] == "drobny"
    assert moved["measured"]["images_total"] == 1287


def test_a_genuinely_systemic_alt_text_problem_keeps_its_severity():
    narr = {"serious_issues": [_wcag("Brak tekstów alternatywnych dla obrazów")]}
    _attach_issue_evidence(narr, _stats(share_images=_ALT_TEXT_SYSTEMIC_SHARE + 0.3))
    assert len(narr["serious_issues"]) == 1
    assert "demoted_from" not in narr["serious_issues"][0]


def test_control_names_are_never_capped_by_share():
    """The asymmetry the fix depends on. Unnamed images are mostly decorative
    icons; one unnamed submit button on a booking form blocks a screen-reader
    user completely. A share cap here would bury the real barrier."""
    narr = {"critical_issues": [_wcag("Brak etykiet przycisków w formularzach")]}
    stats = _stats(share_images=0.01)
    _attach_issue_evidence(narr, stats)
    assert len(narr["critical_issues"]) == 1


def test_a_critical_wcag_claim_with_no_evidence_is_demoted():
    """Enforces the prompt's own rule, which the model did not follow."""
    narr = {"critical_issues": [_wcag("Brak etykiet przycisków")]}
    _attach_issue_evidence(narr, _stats(label_verified=False))
    assert narr["critical_issues"] == []
    assert narr["minor_issues"][0]["demotion_reason"] == "no_evidence"


def test_heuristic_and_measured_issues_outside_wcag_are_left_where_they_are():
    """Scoped to the channel that was shown to break. A measured PageSpeed
    result with no heuristic id must not be demoted by this rule."""
    perf = {"heuristic_id": None, "heuristic_name": "Wydajność techniczna",
            "impact": "LCP 33 s na urządzeniach mobilnych"}
    narr = {"serious_issues": [perf]}
    _attach_issue_evidence(narr, _stats())
    assert narr["serious_issues"] == [perf]


def test_the_join_is_idempotent():
    """resolve_narrative re-applies it on every render — HTML, PDF and Word."""
    narr = {"serious_issues": [_wcag("Brak tekstów alternatywnych")],
            "critical_issues": [_wcag("Brak etykiet przycisków")]}
    stats = _stats()
    _attach_issue_evidence(narr, stats)
    once = copy.deepcopy(narr)
    _attach_issue_evidence(narr, stats)
    assert narr == once


# --- and the reader sees it --------------------------------------------------

def test_the_card_shows_the_measurement_and_the_demotion():
    from simulated_web_agent.main.reporter_html import _render_all_issues

    narr = {"serious_issues": [_wcag("Brak tekstów alternatywnych")]}
    _attach_issue_evidence(narr, _stats())
    html = _render_all_issues(narr)
    assert "Zmierzono: 128 z 1287" in html
    assert "Przeniesiono z kategorii" in html
    assert "HNone" not in html, "a WCAG issue has no Nielsen heuristic to number"
