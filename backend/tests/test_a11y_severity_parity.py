"""Every format shows an accessibility finding at the same severity.

The alt-text rule (below 20% of images unnamed, a minor barrier) was applied by
the HTML and by the score, and not by the Word file: in both archived runs with
measured images, "Brak tekstu alternatywnego" was "drobny" in the HTML and PDF,
"liczone jako drobny" in the score, and "poważny" in the .docx — which is the
copy a client edits and forwards.
"""
from docx import Document

from simulated_web_agent.main import reporter_html
from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.scoring import accessibility_severity, compute_ux_score

IMAGES = {"total": 1603, "unnamed": 145, "share_unnamed": 0.09}
ALT = {"category": "Brak tekstu alternatywnego", "severity": "poważny", "topic": "alt_text",
       "detail": "Obrazy bez atrybutu alt.", "verified": True}


def _stats(images=IMAGES):
    return {"accessibility": {"findings": [ALT], "measured": {"images": images}},
            "heuristic": {}, "prioritization": []}


def test_the_rule_in_one_place():
    assert accessibility_severity(ALT, IMAGES) == (
        "drobny", "Zmierzono: 145 z 1603 obrazów bez tekstu alternatywnego (9%).")
    assert accessibility_severity(ALT, {"share_unnamed": 0.4}) == ("poważny", "")
    assert accessibility_severity(ALT, {}) == ("poważny", ""), "unmeasured: as found"
    other = {**ALT, "topic": "control_name"}
    assert accessibility_severity(other, IMAGES) == ("poważny", "")


def test_html_word_and_score_agree(tmp_path):
    html = reporter_html._render_accessibility_detail(_stats())
    assert ">drobny<" in html and "145 z 1603" in html

    doc = Document(build_docx({"website": "x.pl", "narrative": {}, "statistics": _stats()},
                              tmp_path, tmp_path / "r.docx"))
    paras = [p.text for p in doc.paragraphs]
    i = paras.index("Brak tekstu alternatywnego")
    assert paras[i + 1] == "drobny"
    assert any("145 z 1603" in p for p in paras)

    line = [d for d in compute_ux_score(_stats())["deductions"] if d["kind"] == "accessibility"][0]
    assert line["severity"] == "drobny"
