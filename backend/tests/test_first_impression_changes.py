"""Each first-impression reason comes with a change that would remove it.

The review's complaint about the first-impression section had two halves: no
clear account of what lowered each score, and "Co można zaproponować do
zmiany?". The reasons were added first; this pins the second half. One change
per dimension, answering that dimension's barrier, so the proposal travels with
the reason it answers instead of landing in a separate list the reader has to
match up.
"""
from docx import Document

from simulated_web_agent.agent.prompt_registry import registry as prompts
from simulated_web_agent.main import reporter_html
from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter import aggregate_statistics


def _report(**fi_over):
    fi = {
        "overall_score": 3.0,
        "scores": {"purpose_clarity": 2, "call_to_action": 5},
        "score_rationale": {
            "purpose_clarity": "Baner pokazuje samą promocję — nie widać, czym zajmuje się serwis.",
            "call_to_action": "Przycisk „Konfiguruj” jest widoczny od razu.",
        },
        "suggested_changes": {
            "purpose_clarity": "Dodać pod logo jedno zdanie o ofercie serwisu.",
            "call_to_action": "",
        },
    }
    fi.update(fi_over)
    return {"trace_dir": "runs/R1/p1", "persona": "Persona: Marta\nWiek: 24",
            "first_impression": fi, "heuristic_findings": [],
            "survey_result": {"answers": []}}


def _reasons(report):
    return aggregate_statistics([report])["first_impression"]["dimension_reasons"]


def test_the_change_travels_with_its_reason():
    reasons = _reasons(_report())
    assert reasons["purpose_clarity"][0]["change"].startswith("Dodać pod logo")
    assert reasons["call_to_action"][0]["change"] == "", "a 5 needs no change"


def test_older_runs_keep_the_reason_alone():
    reasons = _reasons(_report(suggested_changes=None))
    assert reasons["purpose_clarity"][0]["change"] == ""
    assert reasons["purpose_clarity"][0]["why"].startswith("Baner")


def test_the_html_shows_the_change_under_the_reason():
    stats = aggregate_statistics([_report()])
    html = reporter_html._render_score_reasons(stats)
    why_at = html.index("Baner pokazuje")
    fix_at = html.index("Propozycja zmiany:")
    assert why_at < fix_at < html.index("Dodać pod logo")
    assert html.count("Propozycja zmiany:") == 1, "no empty proposal for a 5"
    assert "nie zmierzony wynik" in html, "a proposal is labelled as one"


def test_no_proposal_legend_without_proposals():
    stats = aggregate_statistics([_report(suggested_changes=None)])
    html = reporter_html._render_score_reasons(stats)
    assert "Dlaczego takie oceny" in html
    assert "Propozycja zmiany" not in html and "nie zmierzony wynik" not in html


def test_the_word_report_carries_it(tmp_path):
    agg = {"website": "x.pl", "task": "Skonfiguruj auto", "narrative": {},
           "statistics": aggregate_statistics([_report()])}
    doc = Document(build_docx(agg, tmp_path, tmp_path / "r.docx"))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Propozycja zmiany: Dodać pod logo" in text
    assert text.index("Baner pokazuje") < text.index("Propozycja zmiany")


def test_the_prompt_still_formats():
    prompt = prompts.get("first_impression").format(persona="Marta", site_context="Sklep")
    assert '"suggested_changes"' in prompt and "{persona}" not in prompt
