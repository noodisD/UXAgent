"""Report defects found by verifying the first live run on the current code
(2026-09-26 hyundai, each confirmed by an independent check)."""
from simulated_web_agent.main.reporter import (
    _not_assessable, _order_a11y_instances, _render_aggregated_md, resolve_narrative,
)
from simulated_web_agent.main.results_api import _match_issues
from simulated_web_agent.main.scoring import rationale_text


def test_the_rationale_is_written_from_the_breakdown():
    """The narrator's said "3/3 partial" above a line counting 2 of 3."""
    bd = {"score": 3.1, "start": 5.0, "total_deduction": 1.94, "raw": 3.06, "deductions": [
        {"label": "Realizacja zadania", "amount": 0.25},
        {"label": "Dostępność: Interaktywny element bez nazwy", "amount": 0.5}]}
    agg = {"narrative": {"executive_summary": "S.", "score_rationale": "−0.25 za 3/3 persony",
                         "critical_issues": [], "serious_issues": [], "minor_issues": []},
           "statistics": {"score_breakdown": bd, "heuristic": {}}}
    text = resolve_narrative(agg)["score_rationale"]
    assert text == rationale_text(bd)
    assert text.startswith("Ocena 3.1/5: od 5.0 odjęto 1.94 pkt")
    assert text.index("Interaktywny element") < text.index("Realizacja zadania"), "largest first"


def test_no_opportunity_to_assess_is_a_placeholder():
    assert _not_assessable("1", "The session did not involve any payment steps. Therefore, I had "
                                "no opportunity to assess feelings of control regarding payment.")


def _instance(page, sev, detail, who):
    return {"severity": sev, "detail": detail, "persona": who,
            "example": {"url": page, "persona_name": who, "step": 1}}


def test_proof_comes_from_the_barrier_the_entry_describes():
    """The homepage buttons were cited as proof of the test-drive combobox."""
    form, home = "https://x.pl/jazda-testowa.html#!/a", "https://x.pl/pl"
    entry = {"_worst_url": form, "_instances": [
        _instance(form, "krytyczny", "combobox bez etykiety", "Kacper"),
        _instance(home, "krytyczny", "2 przyciski bez dostępnej nazwy", "Michał"),
        _instance(form + "?b", "krytyczny", "combobox bez etykiety", "Michał")]}
    _order_a11y_instances(entry)
    assert [e["url"] for e in entry["examples"][:2]] == [form, form + "?b"]
    assert [o["detail"] for o in entry["other_instances"]] == ["2 przyciski bez dostępnej nazwy"]


def _issue(kind, words):
    return {"kind": kind, "stems": set(words), "text": " ".join(words)}


def test_the_comparison_matches_by_kind_and_content():
    same = [f"stem{i}" for i in range(12)]
    prev = [_issue("wcag:control_name", same), _issue("h:2", ["hiszpa", "język", "polsk"])]
    now = [_issue("wcag:control_name", same[:10] + ["nowe"]), _issue("h:2", ["kolor", "cena", "3000"])]
    fresh, persisting, gone = _match_issues(now, prev)
    assert persisting == [now[0]["text"]]
    assert fresh == [now[1]["text"]], "same Nielsen number, a different defect"
    assert gone == [prev[1]["text"]]


def test_markdown_has_no_hnone_and_uses_each_questions_scale():
    agg = {"website": "x.pl", "pagespeed": {"mobile": {"error": "timed out"}},
           "narrative": {"executive_summary": "S.", "critical_issues": [
               {"heuristic_id": None, "heuristic_name": "Dostępność (WCAG)", "examples": []}],
               "serious_issues": [], "minor_issues": []},
           "statistics": {"participant_count": 1, "heuristic": {}, "survey": {"questions": [
               {"id": "synthetic_seq", "kind": "scale", "prompt": "Łatwość (SEQ)", "avg": 4.67, "n": 3, "max": 7},
               {"id": "q9", "kind": "open", "prompt": "Czego brakowało?", "answers": ["Cennika"]}]}}}
    md = _render_aggregated_md(agg)
    assert "HNone" not in md and "—/5" not in md
    assert "śr. 4.67 / 7" in md
    assert "Pomiar nie powiódł się" in md
