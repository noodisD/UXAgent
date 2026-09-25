"""One accessibility barrier is one finding, however each persona worded it.

In the 2026-09-25 hyundai replay one unnamed combobox on the test-drive form
came back from three personas as three categories — "Brak dostępnej nazwy dla
kontrolki", "Brakujące etykiety kontrolek", "Brak nazwy dla kontrolki" — and
aggregation keyed on the wording, so the score charged it three times (−1.25)
while the narrative called it one issue seen by 3 of 3. 3 of the 8 archived runs
with accessibility findings had such a split.
"""
import pytest

from simulated_web_agent.main.a11y_checks import a11y_topic
from simulated_web_agent.main.reporter import aggregate_statistics
from simulated_web_agent.main.scoring import compute_ux_score


def _report(persona, category, detail, severity="poważny", step=15):
    return {
        "trace_dir": f"runs/R1/{persona}",
        "persona": f"Persona: {persona}",
        "first_impression": {},
        "heuristic_findings": [],
        "accessibility": {"overall_score": 3, "findings": [{
            "category": category, "severity": severity, "detail": detail,
            "recommendation": f"Nadać nazwę ({persona}).",
            "url": "https://www.hyundai.com/pl/pl/jazda-probna.html", "step": step,
            "verified": True,
        }]},
        "survey_result": {"answers": []},
    }


SPLIT = [
    _report("Marcin", "Brak dostępnej nazwy dla kontrolki",
            "Pole wyboru (combobox) bez dostępnej nazwy.", "krytyczny"),
    _report("Piotr", "Brakujące etykiety kontrolek",
            "4 interaktywne kontrolki bez etykiet.", "krytyczny", step=25),
    _report("Oskar", "Brak nazwy dla kontrolki",
            "1 pole wyboru (combobox) bez etykiety.", "poważny", step=16),
]


@pytest.mark.parametrize("text", [
    "Brak dostępnej nazwy dla kontrolki", "Brak dostępnej nazwy kontrolki",
    "Brakujące etykiety kontrolek", "Brak nazwy dla kontrolki",
])
def test_every_wording_of_an_unnamed_control_has_its_topic(text):
    assert a11y_topic(text) == "control_name"


def test_the_split_barrier_is_one_finding_seen_by_all_three():
    findings = aggregate_statistics(SPLIT)["accessibility"]["findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f["personas"] == ["Marcin", "Oskar", "Piotr"]
    assert f["severity"] == "krytyczny", "the worst instance decides"
    assert f["category"] == "Brak dostępnej nazwy dla kontrolki"
    assert len(f["examples"]) == 3, "each persona's proof is kept"


def test_the_score_charges_it_once():
    stats = aggregate_statistics(SPLIT)
    lines = [d for d in compute_ux_score(stats)["deductions"] if d.get("kind") == "accessibility"]
    assert len(lines) == 1


def test_different_barriers_stay_apart():
    reports = [
        SPLIT[0],
        _report("Piotr", "Brak tekstu alternatywnego", "Obrazy bez atrybutu alt."),
        _report("Oskar", "Niski kontrast tekstu", "Kontrast 2.9:1."),
    ]
    topics = sorted(f["topic"] for f in aggregate_statistics(reports)["accessibility"]["findings"])
    assert topics == ["alt_text", "contrast", "control_name"]


def test_a_category_with_no_measured_check_still_groups_by_its_name():
    reports = [
        _report("Marcin", "Pułapka klawiatury", "Fokus zostaje w oknie."),
        _report("Piotr", "Pułapka klawiatury", "Nie da się wyjść klawiszem Tab."),
        _report("Oskar", "Brak widocznego fokusu", "Nie widać, gdzie jest fokus."),
    ]
    findings = aggregate_statistics(reports)["accessibility"]["findings"]
    assert sorted(f["category"] for f in findings) == ["Brak widocznego fokusu", "Pułapka klawiatury"]
