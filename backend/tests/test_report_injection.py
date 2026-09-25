"""The HTML report cannot run anything the model or the site put into it.

The narrative is the aggregate model's JSON, and the model is fed text from the
site under test. Two of its fields were written into the page unescaped — a
recommendation's priority and an issue's severity — and the page is opened on
the API's origin with the API key in its URL. A payload in either field became
a live element whose handler could read the key and call the API; an int
severity crashed the HTML and PDF build outright.
"""
import pytest

from simulated_web_agent.main import app as app_module
from simulated_web_agent.main.reporter_html import generate_html_report

PAYLOAD = '1<img src=x onerror="fetch(\'/runs\')">'


def _agg(**narr):
    base = {"executive_summary": "Podsumowanie.", "overall_ux_score": 3.0,
            "critical_issues": [], "serious_issues": [], "minor_issues": [],
            "priority_recommendations": []}
    base.update(narr)
    return {"website": "x.pl", "task": "Zadanie", "narrative": base,
            "statistics": {"participant_count": 1, "heuristic": {}}}


def test_a_priority_is_text_not_markup():
    html = generate_html_report(_agg(priority_recommendations=[
        {"priority": PAYLOAD, "area": "Koszyk", "action": "Naprawić", "expected_impact": ""}]))
    assert "<img src=x" not in html and "&lt;img src=x" in html


@pytest.mark.parametrize("severity", ['<img src=x onerror=alert(1)>', 2, None, ["poważny"]])
def test_a_severity_the_model_invented_neither_injects_nor_crashes(severity):
    issue = {"heuristic_id": 5, "heuristic_name": "Zapobieganie błędom", "severity": severity,
             "frequency": "1 z 1", "impact": "Opis.", "recommendation": "Zmienić.",
             "verified": True, "examples": []}
    html = generate_html_report(_agg(serious_issues=[issue]))
    assert "<img src=x" not in html


def test_the_served_report_may_not_run_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_module, "_API_KEY", "")
    (tmp_path / "runs" / "R1").mkdir(parents=True)
    (tmp_path / "runs" / "R1" / "aggregated_report.html").write_text("<html></html>", encoding="utf-8")
    app_module.app.config.update(TESTING=True)
    res = app_module.app.test_client().get("/runs/R1/report.html")
    assert res.status_code == 200
    csp = res.headers.get("Content-Security-Policy", "")
    assert "script-src 'none'" in csp and "default-src 'none'" in csp
    assert "img-src 'self'" in csp, "the screenshots are same-origin"
