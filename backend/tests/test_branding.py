"""The Havas logo in the reports, and the promise that it can never cost a report.

White on the HTML/PDF header (a black band); red on the Word file (a white page).
"""
import base64

import pytest
from docx import Document

from simulated_web_agent.main import branding
from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter_html import generate_html_report

AGG = {"website": "x.pl", "task": "Zadanie",
       "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                     "serious_issues": [], "minor_issues": []},
       "statistics": {"participant_count": 2}}


def test_both_logo_files_ship_with_the_package_and_are_real_pngs():
    for variant in ("white", "red"):
        path = branding.logo_path(variant)
        assert path is not None, variant
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_unknown_variant_is_not_a_crash():
    assert branding.logo_path("blue") is None


def test_the_data_uri_decodes_back_to_the_same_file():
    uri = branding.logo_data_uri("white")
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == branding.logo_path("white").read_bytes()


def test_the_html_header_carries_the_white_logo_before_the_title():
    html = generate_html_report(AGG)
    assert 'class="brand-logo"' in html and 'alt="HAVAS"' in html
    assert html.index('class="brand-logo"') < html.index("<h1>Raport UX —")
    assert branding.logo_data_uri("white") in html, "the white variant, on the black band"
    assert branding.logo_data_uri("red") not in html


def test_the_word_file_opens_with_the_red_logo(tmp_path):
    path = build_docx(AGG, tmp_path, tmp_path / "r.docx")
    doc = Document(path)
    assert len(doc.inline_shapes) == 1
    first = doc.element.body[0]
    assert first.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"), \
        "the picture is the first thing on the page"
    rid = doc.inline_shapes[0]._inline.graphic.graphicData.pic.blipFill.blip.embed
    assert doc.part.related_parts[rid].blob == branding.logo_path("red").read_bytes()
    assert 0.9 < doc.inline_shapes[0].width.cm < 4.0, "small, not a banner"


@pytest.fixture
def no_logos(monkeypatch, tmp_path):
    monkeypatch.setattr(branding, "ASSETS", tmp_path / "empty")
    branding.logo_data_uri.cache_clear()
    yield
    branding.logo_data_uri.cache_clear()


def test_a_missing_logo_costs_the_logo_not_the_report(no_logos, tmp_path):
    html = generate_html_report(AGG)
    assert "Raport UX" in html and "brand-logo" not in html.split("</style>", 1)[1], "no broken-image tag"
    doc = Document(build_docx(AGG, tmp_path, tmp_path / "r.docx"))
    assert len(doc.inline_shapes) == 0
    assert any("Raport UX" in p.text for p in doc.paragraphs)
