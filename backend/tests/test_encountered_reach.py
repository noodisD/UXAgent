"""A defect visible on the page reached everyone who saw that page.

"null kW (null KM)" was on the engine step in all three hyundai sessions, and
the report said "1 z 3 uczestników" — reach counted who wrote a finding down,
not who met the defect. Limited on purpose to broken rendering (null,
undefined, NaN, unrendered templates), which anyone on the page sees; "the
button did not work" still counts only who hit it. 1 of 172 archived findings
qualifies — this one.
"""
import pytest

from simulated_web_agent.main.evidence import pages_contain, rendering_artifact
from simulated_web_agent.main.prioritization import prioritize
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.trace_store import TraceStore


@pytest.mark.parametrize("evidence,artifact", [
    ("Moc silnika jako 'null kW (null KM)'.", "null kW (null KM)"),
    ("Cena pokazuje 'NaN zł'.", "NaN zł"),
    ("Tytuł to '{{ product.name }}'.", "{{ product.name }}"),
    ("Etykieta brzmi 'undefined'.", "undefined"),
])
def test_rendering_artifacts_are_recognised(evidence, artifact):
    assert rendering_artifact(evidence) == artifact


@pytest.mark.parametrize("evidence", [
    "Przycisk 'Następny krok' nie reagował.",
    "Wartość null nie powinna tu być, ale strona pokazuje 'Brak danych'.",
    "Nie ma żadnego cytatu.",
])
def test_ordinary_findings_are_not(evidence):
    """Only a *quoted* fragment counts, so prose that merely says "null" does not."""
    assert rendering_artifact(evidence) == ""


def test_pages_are_searched_for_the_fragment(tmp_path):
    store = TraceStore(tmp_path)
    store.write_simp_html(3, "<div>Hybrydowy 1.6 GDI <b>null kW (null KM)</b></div>")
    assert pages_contain(store, "null kW (null KM)") is True
    assert pages_contain(store, "EXECUTIVE MY27") is False


def test_reach_includes_those_whose_pages_showed_it():
    finding = {"heuristic_id": 9, "severity": "poważny", "confidence": "high",
               "_persona_dir": "a", "_encountered_by": {"a", "b", "c"}}
    row = prioritize([finding], {"a": "success", "b": "success", "c": "partial"})[0]
    assert row["personas_affected"] == 3 and row["personas_reported"] == 1


def test_the_score_line_says_reported_versus_seen():
    stats = {"prioritization": [{"heuristic_id": 9, "heuristic_name": "Błędy", "severity": "poważny",
                                 "personas_affected": 3, "personas_reported": 1,
                                 "participant_count": 3, "confidence": "high"}],
             "heuristic": {}, "accessibility": {}}
    line = compute_ux_score(stats)["deductions"][0]
    assert "zgłosił 1, na stronie widoczne u 3" in line["basis"]
    assert line["amount"] == 0.5, "full reach: 0.5 × 1.0"
