"""Every format of a report is what the current code renders.

The HTML was written once at aggregation and served as it was; the PDF was
printed from it; the Word file was built by current code on first download and
then cached. So a renderer fix never reached an older run's HTML or PDF, a
.docx built before a fix was never rebuilt, and one run could ship two sets of
numbers: 4 of 9 archived reports had different header counts in the served
HTML and a current render.
"""
import json
import os

from simulated_web_agent.main import app as app_module
from simulated_web_agent.main.report_docx import ensure_report_docx
from simulated_web_agent.main.reporter_html import generate_html_report, refresh_html_report

AGG = {"website": "x.pl", "task": "Zadanie",
       "narrative": {"executive_summary": "Aktualne podsumowanie.", "overall_ux_score": 3,
                     "critical_issues": [], "serious_issues": [], "minor_issues": [],
                     "priority_recommendations": []},
       "statistics": {"participant_count": 2, "heuristic": {}}}


def _run(tmp_path, stale_html=None):
    d = tmp_path / "runs" / "R1"
    d.mkdir(parents=True)
    (d / "aggregated_report.json").write_text(json.dumps(AGG), encoding="utf-8")
    if stale_html is not None:
        (d / "aggregated_report.html").write_text(stale_html, encoding="utf-8")
    return d


def test_a_stale_html_file_is_replaced(tmp_path):
    d = _run(tmp_path, stale_html="<html>stary raport</html>")
    out = refresh_html_report(d)
    assert out.read_text(encoding="utf-8") == generate_html_report(AGG)


def test_an_unchanged_report_keeps_its_file_so_the_pdf_is_not_reprinted(tmp_path):
    d = _run(tmp_path)
    out = refresh_html_report(d)
    os.utime(out, (1_000_000, 1_000_000))
    refresh_html_report(d)
    assert out.stat().st_mtime == 1_000_000


def test_without_json_the_stored_file_is_served(tmp_path):
    d = tmp_path / "runs" / "R1"
    d.mkdir(parents=True)
    (d / "aggregated_report.html").write_text("<html>legacy</html>", encoding="utf-8")
    assert refresh_html_report(d).read_text(encoding="utf-8") == "<html>legacy</html>"
    assert refresh_html_report(tmp_path / "nothing") is None


def test_the_endpoint_serves_the_current_render(tmp_path, monkeypatch):
    _run(tmp_path, stale_html="<html>stary raport</html>")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_module, "_API_KEY", "")
    app_module.app.config.update(TESTING=True)
    body = app_module.app.test_client().get("/runs/R1/report.html").get_data(as_text=True)
    assert "Aktualne podsumowanie." in body and "stary raport" not in body


def test_a_docx_newer_than_its_json_is_still_rebuilt(tmp_path):
    d = _run(tmp_path)
    old = d / "aggregated_report.docx"
    old.write_bytes(b"not a docx built by today's code")
    future = old.stat().st_mtime + 3600
    os.utime(old, (future, future))
    out = ensure_report_docx(d)
    assert out.read_bytes()[:2] == b"PK", "a real .docx, rebuilt"
