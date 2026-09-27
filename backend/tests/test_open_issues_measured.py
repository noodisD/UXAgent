"""Open issues from verifying the 2026-09-26 hyundai run: what an unnamed image
is, what an unnamed field costs, a hint read as a failure, and one spelling of
an average."""
from pathlib import Path

from simulated_web_agent.main.a11y_checks import (
    _NODE, classify_unnamed_images, describe_unnamed_controls, summarise_pages,
)
from simulated_web_agent.main.experiment import ADVISORY_PREFIX, _revisit_warning
from simulated_web_agent.main.reporter import _wcag_demotion_reason
from simulated_web_agent.main.reporter_html import avg_text
from simulated_web_agent.main.scoring import (
    accessibility_severity, alt_text_measured_text, alt_text_share,
)

_PROMPTS = Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"

SNAPSHOT = """\
- banner:
  - button "Flyout":
    - img
  - button "previous slides" [disabled]:
    - img
- main:
  - article:
    - img
  - link "IONIQ 5":
    - img "IONIQ 5"
- img
"""


def test_unnamed_images_are_split_by_where_they_sit():
    """90 of 517 "without alt text" were 65 icons in named buttons and 23
    top-level pixels; the report said blind users lose the car models."""
    assert classify_unnamed_images(SNAPSHOT) == {
        "in_named_control": 2, "outside_content": 1, "content": 1}


def test_the_split_is_summed_over_pages():
    page = {"images": {"total": 50, "unnamed": 10, "unnamed_in_named_control": 8,
                       "unnamed_outside_content": 1, "unnamed_content": 1},
            "controls": {"total": 5, "unnamed": 0}}
    images = summarise_pages({"a": page, "b": page})["images"]
    assert images["unnamed_content"] == 2 and images["unnamed_in_named_control"] == 16
    assert images["share_unnamed"] == 0.2 and images["share_unnamed_content"] == 0.02


def _alt(share_content=0.004):
    return {"total": 517, "unnamed": 90, "unnamed_in_named_control": 65,
            "unnamed_outside_content": 23, "unnamed_content": 2,
            "share_unnamed": 0.174, "share_unnamed_content": share_content}


def test_severity_follows_the_images_that_carry_content():
    sev, text = accessibility_severity({"topic": "alt_text", "severity": "poważny"}, {
        **_alt(), "share_unnamed": 0.5})
    assert sev == "drobny"
    assert "90 z 517" in text and "Z tego:" in text
    assert "ikony w przyciskach lub linkach, które mają własną nazwę — 65" in text
    assert text.endswith("Obrazy w treści strony bez opisu: 2 (0,4%).")


def test_a_run_measured_before_the_split_keeps_its_rule():
    legacy = {"total": 100, "unnamed": 50, "share_unnamed": 0.5}
    assert alt_text_share(legacy) == 0.5
    assert accessibility_severity({"topic": "alt_text", "severity": "poważny"}, legacy)[0] == "poważny"
    assert alt_text_measured_text(legacy) == (
        "Zmierzono: 50 z 100 obrazów bez tekstu alternatywnego (50%).")


def test_an_issue_is_demoted_on_the_content_share():
    issue = {"heuristic_id": None, "heuristic_name": "Dostępność (WCAG)", "verified": True,
             "measured": {"images_total": 517, "images_unnamed": 300,
                          "share_unnamed": 0.58, "share_unnamed_content": 0.004}}
    assert _wcag_demotion_reason(issue, "serious_issues") == "measured_share"


def test_nodes_with_attributes_are_parsed():
    """`- button "previous slides" [disabled]:` was not a node, so the icon
    inside it counted as missing content."""
    for line, role, name in (('  - option "IONIQ 5" [selected]', "option", "IONIQ 5"),
                             ('- button "previous slides" [disabled]:', "button", "previous slides"),
                             ("- checkbox [checked]", "checkbox", None),
                             ('- \'link "Hyundai: Home" [cursor=pointer]\':', "link", "Hyundai: Home")):
        m = _NODE.match(line)
        assert m and m.group("role") == role and m.group("name") == name, line


def test_an_unnamed_field_carries_its_visible_label_and_value():
    """Written up as blocking the test-drive form, while the page printed
    "Model" above it and it already held "IONIQ 5"."""
    snap = """\
- main:
  - text: Model Mandatory Field
  - combobox:
    - option "i10"
    - option "IONIQ 5" [selected]
  - button "Wyślij"
"""
    assert describe_unnamed_controls(snap) == [(
        "combobox",
        'visible text before it: "Model Mandatory Field"; selected option: "IONIQ 5"')]


def test_the_accessibility_prompt_does_not_let_a_missing_name_block_a_form():
    text = (_PROMPTS / "audit_prompts" / "accessibility_summarize.txt").read_text(encoding="utf-8")
    assert "where_on_worst_page" in text and "A missing name alone does not show" in text
    agg = (_PROMPTS / "audit_prompts" / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert "unnamed_content" in agg and "zgadzać się z TASK COMPLETION" in agg


def test_the_revisit_hint_says_the_click_worked():
    """"You have clicked X 3 times" in the error channel was narrated as a dead
    button: the feedback prompt says a set error means the action FAILED."""
    _thought, error = _revisit_warning(2, "Konfiguruj")
    assert error.startswith(ADVISORY_PREFIX) and ADVISORY_PREFIX.startswith("Advisory")
    for prompt in ("feedback.txt", "action.txt"):
        text = (_PROMPTS / "agent" / "shop_prompts" / prompt).read_text(encoding="utf-8")
        assert 'starts with "Advisory"' in text, prompt


def test_one_spelling_for_an_average():
    assert [avg_text(v) for v in (4, 3.07, 1.0, None, True)] == ["4,00", "3,07", "1,00", "—", "—"]
