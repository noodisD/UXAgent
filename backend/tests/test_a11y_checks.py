"""Deterministic accessibility checks over the captured aria snapshot.

"Is this image missing its alt text?" is a decidable question. Asking an LLM to
answer it by reading the whole accessibility tree was both unreliable and
enormous: one pepco.pl homepage snapshot is 377,000 characters, and a 30-step
session produced a 1.66M-char (~416k token) prompt that failed near the context
limit — which is what cost one persona their entire accessibility audit in run
2026-07-16_13-41-14.

Code decides what is broken; the LLM only narrates and scores what code found.

Input is Playwright's aria-snapshot text, e.g.

    - banner "Site header":
      - img
      - link "Znajdź sklep Pepco":
        - /url: /store-locator
      - 'combobox "Language selector. Currently selected: Polski"':
        - option "Polski"
"""
from simulated_web_agent.main.a11y_checks import (
    extract_a11y_candidates,
    parse_aria_snapshot,
)
from simulated_web_agent.main.trace_store import TraceStore


def _store(tmp_path, snapshots, font_sizes=None):
    store = TraceStore(tmp_path / "persona")
    for step, text in snapshots.items():
        store.write_a11y_snapshot(step, text)
    for step, sizes in (font_sizes or {}).items():
        store.write_font_sizes(step, sizes)
    return store


# --- parsing -------------------------------------------------------------

def test_parses_role_and_name():
    nodes = parse_aria_snapshot('- link "Znajdź sklep Pepco":\n  - /url: /store-locator')
    assert nodes[0].role == "link"
    assert nodes[0].name == "Znajdź sklep Pepco"


def test_parses_bare_role_with_no_name():
    nodes = parse_aria_snapshot("- img")
    assert nodes[0].role == "img"
    assert nodes[0].name == ""


def test_parses_quoted_role_form():
    """Playwright wraps the whole node in single quotes when the name contains
    punctuation — the role must still come out as `combobox`."""
    line = "- 'combobox \"Language selector. Currently selected: Polski\"':"
    nodes = parse_aria_snapshot(line)
    assert nodes[0].role == "combobox"
    assert nodes[0].name.startswith("Language selector")


def test_ignores_non_node_lines():
    nodes = parse_aria_snapshot("- /url: /store-locator\n- text: Sprawdź kolekcje")
    assert [n.role for n in nodes] == []


# --- checks --------------------------------------------------------------

def test_image_without_alt_is_a_candidate(tmp_path):
    store = _store(tmp_path, {0: '- img\n- img "Logo Pepco"'})
    result = extract_a11y_candidates(store)
    kinds = {c["check"] for c in result["candidates"]}
    assert "image_missing_alt" in kinds


def test_image_with_alt_is_not_flagged(tmp_path):
    store = _store(tmp_path, {0: '- main:\n  - img "Logo Pepco"'})
    result = extract_a11y_candidates(store)
    assert "image_missing_alt" not in {c["check"] for c in result["candidates"]}


def test_unlabelled_control_is_a_candidate(tmp_path):
    store = _store(tmp_path, {0: '- button\n- button "Szukaj"'})
    result = extract_a11y_candidates(store)
    c = [x for x in result["candidates"] if x["check"] == "control_missing_name"]
    assert len(c) == 1
    assert c[0]["role"] == "button"


def test_missing_main_landmark_is_a_candidate(tmp_path):
    store = _store(tmp_path, {0: '- banner "Site header":\n  - img "Logo"'})
    result = extract_a11y_candidates(store)
    assert "missing_main_landmark" in {c["check"] for c in result["candidates"]}


def test_main_landmark_present_is_not_flagged(tmp_path):
    store = _store(tmp_path, {0: '- main:\n  - img "Logo"'})
    result = extract_a11y_candidates(store)
    assert "missing_main_landmark" not in {c["check"] for c in result["candidates"]}


def test_small_text_uses_the_font_sizes_already_collected(tmp_path):
    store = _store(tmp_path, {0: "- main:"}, font_sizes={0: [11, 14, 16]})
    result = extract_a11y_candidates(store)
    small = [c for c in result["candidates"] if c["check"] == "text_below_12px"]
    assert small and small[0]["min_size_px"] == 11


def test_text_at_or_above_12px_is_not_flagged(tmp_path):
    store = _store(tmp_path, {0: "- main:"}, font_sizes={0: [12, 16]})
    result = extract_a11y_candidates(store)
    assert not [c for c in result["candidates"] if c["check"] == "text_below_12px"]


# --- the reason this module exists ---------------------------------------

def test_identical_repeats_collapse_to_one_candidate_with_a_count(tmp_path):
    """Carousels repeat the same unlabelled image dozens of times. Reporting
    each one is what made the payload unusable — and they are one defect."""
    store = _store(tmp_path, {0: "\n".join(["- img"] * 40)})
    result = extract_a11y_candidates(store)
    imgs = [c for c in result["candidates"] if c["check"] == "image_missing_alt"]
    assert len(imgs) == 1
    assert imgs[0]["occurrences"] == 40


def test_revisiting_a_page_does_not_inflate_the_defect_count(tmp_path):
    """A revisited page is snapshotted again, so `occurrences` double-counts the
    same images. On the real pepco trace that reads as 1760 — stating it as a
    defect count would be a fabricated claim, so the per-page figure and the
    spread must both be available."""
    store = _store(tmp_path, {0: "- img\n- img", 1: "- img\n- img", 2: "- img\n- img"})
    c = [x for x in extract_a11y_candidates(store)["candidates"]
         if x["check"] == "image_missing_alt"][0]
    assert c["occurrences"] == 6        # total matches across snapshots
    assert c["max_on_one_page"] == 2    # what a user actually meets
    assert c["steps_affected"] == 3


def test_one_defect_class_is_reported_once_across_steps(tmp_path):
    """Small text on every page is one finding on N pages, not N findings."""
    store = _store(tmp_path, {0: "- main:", 1: "- main:", 2: "- main:"},
                   font_sizes={0: [11], 1: [10], 2: [11]})
    small = [c for c in extract_a11y_candidates(store)["candidates"]
             if c["check"] == "text_below_12px"]
    assert len(small) == 1
    assert small[0]["min_size_px"] == 10   # worst case across pages
    assert small[0]["steps_affected"] == 3


def test_candidates_stay_small_for_a_huge_snapshot(tmp_path):
    """The whole point: what reaches the LLM must not scale with page size."""
    huge = "\n".join(f'- link "Produkt {i}":\n  - /url: /p/{i}\n- img' for i in range(3000))
    store = _store(tmp_path, {0: huge})
    result = extract_a11y_candidates(store)
    import json
    assert len(json.dumps(result, ensure_ascii=False)) < 4000


def test_every_candidate_cites_a_step(tmp_path):
    """Findings must stay traceable — evidence.py resolves the citation."""
    store = _store(tmp_path, {0: "- main:", 3: "- img"})
    result = extract_a11y_candidates(store)
    assert all(isinstance(c["step"], int) for c in result["candidates"])
    assert {c["step"] for c in result["candidates"] if c["check"] == "image_missing_alt"} == {3}


def test_cited_step_is_the_page_the_count_is_true_of(tmp_path):
    """`step` and `max_on_one_page` land in the same sentence, so they must
    describe the same page. Citing first-sighting while reporting the worst
    page's count made the narrator write "1436 images on step 0" when step 0
    had 40 — a fabricated claim built from two true numbers."""
    store = _store(tmp_path, {
        0: "- img\n- img",                  # first sighting, 2
        7: "\n".join(["- img"] * 9),        # the worst page, 9
    })
    c = [x for x in extract_a11y_candidates(store)["candidates"]
         if x["check"] == "image_missing_alt"][0]
    assert c["max_on_one_page"] == 9
    assert c["step"] == 7


def test_clean_page_yields_no_candidates(tmp_path):
    store = _store(tmp_path, {0: '- main:\n  - img "Logo"\n  - button "Szukaj"'},
                   font_sizes={0: [14, 16]})
    result = extract_a11y_candidates(store)
    assert result["candidates"] == []
