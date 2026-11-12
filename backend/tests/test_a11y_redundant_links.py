"""Decorative duplicate links are not serious accessibility issues.

In the 2026-11-10 bnpparibas.pl run the report deducted a "poważny" 0.25 for
"Brak nazwy dostępnej dla linku". The unnamed links were teaser image links
(tabindex="-1", alt="") that sit beside a named link to the same address; the
controls that really lack a name were a slider, two comboboxes and a text field,
filed under the same line, whose label said "link". The counts were right (34 of
3730 controls); the classification and the wording were not.
"""
import shutil
from pathlib import Path

import pytest

from simulated_web_agent.main import a11y_checks, auditor
from simulated_web_agent.main.a11y_checks import (
    a11y_topic, classify_unnamed_controls, extract_a11y_candidates,
    extract_unnamed_link_findings, measure_names, merge_pages,
)
from simulated_web_agent.main.reporter import aggregate_statistics
from simulated_web_agent.main.scoring import RULES, compute_ux_score
from simulated_web_agent.main.trace_store import TraceStore

PAGE = "https://www.bank.pl/kredyty"

UNNAMED_TWIN = "  - link:\n    - /url: /kredyty/hipoteczny\n"
ARIA = ('- main:\n  - heading "Oferta" [level=1]\n'
        '  - link "Kredyt hipoteczny":\n    - /url: /kredyty/hipoteczny\n' + UNNAMED_TWIN)


def _page(*anchors):
    return ('<html><body><main><h1>Oferta</h1>'
            '<a href="/kredyty/hipoteczny">Kredyt hipoteczny</a>' + "".join(anchors) +
            "</main></body></html>")


def _store(tmp_path, aria=ARIA, raw=None, url=PAGE, name="persona", steps=1):
    store = TraceStore(tmp_path / name)
    for step in range(steps):
        store.append_observation({"tabs": [{"url": url, "is_active": True}], "error": None})
        store.write_a11y_snapshot(step, aria)
        if raw is not None:
            store.write_raw_html(step, raw)
    return store


def _kinds(aria, raw):
    return [c["kind"] for c in classify_unnamed_controls(aria, raw, PAGE)]


def _candidate_roles(store):
    return {c["role"] for c in extract_a11y_candidates(store)["candidates"]
            if c["check"] == "control_missing_name"}


IMAGE_TWIN = '<a href="/kredyty/hipoteczny" tabindex="-1"><img src="/a.png" alt=""></a>'


# --- redundant twins ----------------------------------------------------------

def test_an_image_twin_out_of_tab_order_is_redundant_and_not_a_serious_candidate(tmp_path):
    store = _store(tmp_path, raw=_page(IMAGE_TWIN))
    assert _kinds(ARIA, _page(IMAGE_TWIN)) == ["redundant"]
    assert "link" not in _candidate_roles(store), "the narrator is not handed it as an unnamed control"
    (finding,) = extract_unnamed_link_findings(store)
    assert finding["category"] == "Zduplikowany link graficzny bez nazwy"
    assert finding["severity"] == "drobny" and finding["topic"] == "redundant_link"
    assert finding["instances"] == 1 and finding["pages_affected"] == 1
    assert "„/kredyty/hipoteczny”" in finding["detail"]


def test_the_same_link_without_tabindex_and_with_an_image_that_has_no_alt_is_serious(tmp_path):
    raw = _page('<a href="/kredyty/hipoteczny"><img src="/a.png"></a>')
    store = _store(tmp_path, raw=raw)
    assert _kinds(ARIA, raw) == ["serious"], "no alt attribute: the author said nothing about it"
    assert "link" in _candidate_roles(store)
    assert extract_unnamed_link_findings(store) == []


@pytest.mark.parametrize("content", ['<img src="/a.png" alt="Kredyt">', "<svg></svg>"])
def test_being_out_of_tab_order_is_enough_for_a_twin(content):
    """A snapshot taken before a lazy image loaded lists the link as unnamed while the
    stored markup already gives its image an alt: still a twin, not a serious link."""
    assert _kinds(ARIA, _page(f'<a href="/kredyty/hipoteczny" tabindex="-1">{content}</a>')) == ["redundant"]


@pytest.mark.parametrize("attrs", ["", ' tabindex="0"', ' role="presentation"'])
def test_a_twin_that_stays_in_tab_order_is_serious_whatever_image_it_wraps(attrs):
    """A focusable link with no name is a keyboard stop with no name (WCAG F89); the
    named link to the same address does not remove that stop. Only a twin the keyboard
    and the screen reader both skip is redundant."""
    raw = _page(f'<a href="/kredyty/hipoteczny"{attrs}><img src="/a.png" alt=""></a>')
    assert _kinds(ARIA, raw) == ["serious"]


def test_a_hidden_twin_is_redundant_whatever_it_wraps():
    raw = _page('<a href="/kredyty/hipoteczny" aria-hidden="true"><svg></svg></a>')
    assert _kinds(ARIA, raw) == ["redundant"]


def test_a_twin_needs_a_named_link_to_the_same_address():
    aria = '- main:\n  - link "Inne":\n    - /url: /inne\n  - link:\n    - /url: /kredyty/hipoteczny\n'
    raw = ('<html><body><a href="/inne">Inne</a>' + IMAGE_TWIN + "</body></html>")
    assert _kinds(aria, raw) == ["serious"], "nothing else names this destination"


@pytest.mark.parametrize("placeholder", ["#", "#!", "javascript:void(0)"])
def test_two_links_that_share_a_placeholder_address_are_not_twins(placeholder):
    """href="#" and javascript: name no destination: an icon-only carousel arrow wired to
    '#' is a real unnamed control, not a twin of a menu link that also says '#'."""
    aria = (f'- main:\n  - link "Menu":\n    - /url: "{placeholder}"\n'
            f'  - link:\n    - /url: "{placeholder}"\n')
    raw = (f'<html><body><a href="{placeholder}">Menu</a>'
           f'<a href="{placeholder}" tabindex="-1"><img src="/a.svg" alt=""></a></body></html>')
    assert _kinds(aria, raw) == ["serious"]


def test_a_fragment_that_names_a_section_is_a_destination():
    aria = '- main:\n  - link "Kontakt":\n    - /url: "#kontakt"\n  - link:\n    - /url: "#kontakt"\n'
    raw = ('<html><body><a href="#kontakt">Kontakt</a>'
           '<a href="#kontakt" tabindex="-1"><img src="/a.svg" alt=""></a></body></html>')
    assert _kinds(aria, raw) == ["redundant"]


def test_a_real_unnamed_link_on_the_same_address_is_not_hidden_behind_a_twin():
    """Two unnamed links to one address, one of them a plain image link: only the twin
    is redundant. With one unnamed link shown the serious reading wins."""
    plain = '<a href="/kredyty/hipoteczny"><svg></svg></a>'
    two = ARIA + UNNAMED_TWIN
    assert sorted(_kinds(two, _page(IMAGE_TWIN, plain))) == ["redundant", "serious"]
    assert _kinds(ARIA, _page(IMAGE_TWIN, plain)) == ["serious"]


def test_without_a_raw_capture_nothing_changes(tmp_path):
    store = _store(tmp_path, raw=None)
    assert _kinds(ARIA, "") == ["unknown"]
    assert "link" in _candidate_roles(store), "unknown counts as before: an unnamed link"
    assert extract_unnamed_link_findings(store) == []


def test_the_link_candidate_keeps_its_count_when_only_some_are_redundant(tmp_path):
    aria = ARIA + UNNAMED_TWIN
    raw = _page(IMAGE_TWIN, '<a href="/kredyty/hipoteczny"><svg></svg></a>')
    store = _store(tmp_path, aria=aria, raw=raw)
    (link,) = [c for c in extract_a11y_candidates(store)["candidates"] if c.get("role") == "link"]
    assert link["max_on_one_page"] == 1 and link["pages_affected"] == 1


# --- the serious category keeps what is really unnamed ----------------------------

def test_a_slider_without_a_name_stays_serious_beside_redundant_links(tmp_path):
    aria = ARIA + "  - slider\n  - combobox:\n    - option \"A\" [selected]\n  - textbox\n"
    store = _store(tmp_path, aria=aria, raw=_page(IMAGE_TWIN))
    assert _candidate_roles(store) == {"slider", "combobox", "textbox"}
    assert [f["category"] for f in extract_unnamed_link_findings(store)] == [
        "Zduplikowany link graficzny bez nazwy"]


def test_an_unnamed_button_outside_a_notice_stays_serious(tmp_path):
    aria = "- main:\n  - button\n"
    raw = "<html><body><main><button><svg></svg></button></main></body></html>"
    store = _store(tmp_path, aria=aria, raw=raw)
    assert _kinds(aria, raw) == ["serious"]
    assert "button" in _candidate_roles(store)


# --- the consent notice -------------------------------------------------------------

BANNER_ARIA = '- main:\n  - link:\n    - /url: "#"\n'


ACCEPT = '<button id="onetrust-accept-btn-handler">Akceptuję</button>'


def test_a_bare_hash_link_in_a_consent_notice_is_its_own_item(tmp_path):
    raw = ('<html><body><div id="onetrust-banner-sdk"><a href="#"><span class="x"></span></a>'
           + ACCEPT + "</div></body></html>")
    store = _store(tmp_path, aria=BANNER_ARIA, raw=raw)
    assert _kinds(BANNER_ARIA, raw) == ["consent"]
    assert "link" not in _candidate_roles(store)
    (finding,) = extract_unnamed_link_findings(store)
    assert finding["category"] == "Baner zgody na pliki cookie: elementy bez nazwy"
    assert finding["topic"] == "consent_notice" and finding["severity"] == "drobny"
    assert "link" in finding["detail"] and "baner" in finding["detail"].lower()


def test_the_same_link_elsewhere_on_the_page_is_not_a_consent_item():
    raw = '<html><body><div class="menu"><a href="#"><span></span></a></div></body></html>'
    assert _kinds(BANNER_ARIA, raw) == ["serious"]


def test_a_notice_hidden_after_dismissal_does_not_take_a_visible_link_for_its_own():
    raw = ('<html><body><div id="cookie-bar" style="display: none"><a href="#"><i></i></a></div>'
           '<a href="#"><i></i></a></body></html>')
    assert _kinds(BANNER_ARIA, raw) == ["serious"]


def test_a_hidden_notice_does_not_explain_a_link_the_page_shows():
    """The snapshot lists only what is on screen: a banner the page has hidden cannot be
    what it saw, so the link stays unclassified (counted as serious), not a consent item."""
    raw = '<html><body><div id="cookie-bar" style="display: none"><a href="#"><i></i></a></div></body></html>'
    assert _kinds(BANNER_ARIA, raw) == ["unknown"]


def test_an_unnamed_button_in_a_notice_is_a_consent_item():
    aria = "- main:\n  - button\n"
    raw = ('<html><body><div class="cookie-consent"><button><svg></svg></button>'
           "<button>Zaakceptuj wszystkie</button></div></body></html>")
    assert _kinds(aria, raw) == ["consent"]


def test_a_container_named_for_cookies_is_a_notice_only_with_an_acceptance_control():
    """experiment.consent_present requires one: a name alone made pepco's footer cookie-policy
    link a banner that was not there."""
    raw = '<html><body><div id="onetrust-banner-sdk"><a href="#"><span class="x"></span></a></div></body></html>'
    assert _kinds(BANNER_ARIA, raw) == ["serious"]


@pytest.mark.parametrize("raw", [
    '<footer class="footer-cookie-info"><a href="/cookies"><i class="icon"></i></a></footer>',
    '<div><a class="cookie-settings" href="/cookies"><i class="icon"></i></a></div>',
    '<div class="consent-given"><main><a href="/cookies"><i class="icon"></i></a></main></div>',
])
def test_an_unnamed_link_near_a_cookie_policy_is_not_a_consent_item(raw):
    aria = "- main:\n  - link:\n    - /url: /cookies\n"
    assert _kinds(aria, f"<html><body>{raw}</body></html>") == ["serious"]


def test_a_dialog_is_a_notice_only_when_it_holds_an_acceptance_control():
    aria = "- main:\n  - button\n"
    modal = '<div role="dialog"><button><svg></svg></button></div>'
    notice = ('<div role="dialog"><button><svg></svg></button>'
              "<button>Zaakceptuj wszystkie</button></div>")
    assert _kinds(aria, f"<html><body>{modal}</body></html>") == ["serious"]
    assert _kinds(aria, f"<html><body>{notice}</body></html>") == ["consent"]


def test_the_document_root_is_never_the_notice():
    raw = '<html class="cookies-accepted"><body class="consent-given"><a href="#"><i></i></a></body></html>'
    assert _kinds(BANNER_ARIA, raw) == ["serious"]


def test_the_notice_selectors_are_the_harness_own():
    """Read from experiment.py, not copied: if they stop loading, consent items silently
    fall back to being unnamed controls, so this has to fail loudly."""
    consent = a11y_checks._consent()
    assert consent.containers and consent.accepts and consent.labels
    assert consent.consentish.search("usercentrics") and not consent.consentish.search("footer")


# --- counted once per page and element ---------------------------------------------

def test_a_page_snapshotted_again_is_the_same_elements(tmp_path):
    store = _store(tmp_path, raw=_page(IMAGE_TWIN), steps=3)
    (finding,) = extract_unnamed_link_findings(store)
    assert finding["instances"] == 1 and finding["pages_affected"] == 1
    assert "jedynej odwiedzonej stronie" in finding["detail"]


def test_instances_and_pages_both_appear_for_several_pages(tmp_path):
    store = TraceStore(tmp_path / "p")
    for step, url in enumerate([PAGE, PAGE + "/inne", PAGE]):
        store.append_observation({"tabs": [{"url": url, "is_active": True}], "error": None})
        store.write_a11y_snapshot(step, ARIA)
        store.write_raw_html(step, _page(IMAGE_TWIN))
    (finding,) = extract_unnamed_link_findings(store)
    assert finding["instances"] == 2 and finding["pages_affected"] == 2
    assert "Łącznie 2 takie linki na 2 z 2 odwiedzonych stron" in finding["detail"]


def test_the_same_page_opened_by_several_participants_counts_once(tmp_path):
    raw = _page(IMAGE_TWIN)
    a = measure_names(_store(tmp_path, raw=raw, name="a"))
    b = measure_names(_store(tmp_path, raw=raw, name="b"))
    merged = merge_pages([a, b])
    controls = merged["controls"]
    assert controls["unnamed"] == 1 and controls["unnamed_redundant_link"] == 1
    assert controls["pages_with_unnamed_redundant_link"] == 1
    assert controls["unnamed_serious"] == 0 and controls["pages_with_unnamed_serious"] == 0
    assert merged["pages_counted"] == 1


def _participant_report(store):
    """What reporter aggregates: the participant's own code-decided findings."""
    return {"trace_dir": f"runs/R1/{store.trace_dir.name}", "persona": f"Persona: {store.trace_dir.name}",
            "first_impression": {}, "heuristic_findings": [], "survey_result": {"answers": []},
            "accessibility": {"overall_score": 3, "findings": [
                {**f, "verified": True} for f in extract_unnamed_link_findings(store)]}}


def test_the_merged_entry_states_the_audits_count_not_the_first_participants(tmp_path):
    """The aggregate keeps the first participant's detail. Its closing sentence counted that
    participant's pages ("6 takich linków na 2 z 9") next to the score line's 26 on 10."""
    first = _store(tmp_path, raw=_page(IMAGE_TWIN), name="a")
    second = TraceStore(tmp_path / "b")
    for step, url in enumerate([PAGE + "/inne", PAGE + "/jeszcze"]):
        second.append_observation({"tabs": [{"url": url, "is_active": True}], "error": None})
        second.write_a11y_snapshot(step, ARIA)
        second.write_raw_html(step, _page(IMAGE_TWIN))
    stats = aggregate_statistics([_participant_report(first), _participant_report(second)],
                                 stores=[first, second])
    (entry,) = [f for f in stats["accessibility"]["findings"] if f["topic"] == "redundant_link"]
    controls = stats["accessibility"]["measured"]["controls"]
    assert controls["unnamed_redundant_link"] == 3 and controls["pages_with_unnamed_redundant_link"] == 3
    assert entry["detail"].endswith("Łącznie 3 takie linki na 3 z 3 odwiedzonych stron.")
    assert "jedynej" not in entry["detail"] and entry["detail"].count("Łącznie") == 1


def test_restating_leaves_a_detail_the_scan_did_not_write_and_a_run_without_counts():
    prose = {"topic": "redundant_link", "detail": "Model wrote this. Łącznie 2 linki w kodzie."}
    findings = [dict(prose), {"topic": "consent_notice", "detail": "Baner. Występuje na jedynej odwiedzonej stronie."}]
    a11y_checks.restate_spread(findings, {"controls": {"unnamed_redundant_link": 5}, "pages_counted": 4})
    assert findings[0] == prose, "no closing sentence of the scan's own: nothing to restate"
    assert findings[1]["detail"] == "Baner. Występuje na jedynej odwiedzonej stronie.", "no measured count"
    a11y_checks.restate_spread(findings, None)
    assert findings[1]["detail"].endswith("jedynej odwiedzonej stronie.")


def test_the_consent_entry_is_restated_the_same_way():
    findings = [{"topic": "consent_notice",
                 "detail": "W banerze. Łącznie 1 taki element na 1 z 9 odwiedzonych stron."}]
    a11y_checks.restate_spread(findings, {"controls": {"unnamed_consent": 4, "pages_with_unnamed_consent": 3},
                                          "pages_counted": 10})
    assert findings[0]["detail"] == "W banerze. Łącznie 4 takie elementy na 3 z 10 odwiedzonych stron."


def test_the_totals_do_not_move(tmp_path):
    m = measure_names(_store(tmp_path, raw=_page(IMAGE_TWIN)))
    assert m["controls"]["total"] == 2 and m["controls"]["unnamed"] == 1


# --- images -------------------------------------------------------------------------

def _images(tmp_path, raw, aria="- main:\n  - heading \"X\" [level=1]\n  - img\n"):
    m = measure_names(_store(tmp_path, aria=aria, raw=raw))["images"]
    return m["unnamed_content"], m["unnamed_content_decorative"]


def test_a_css_sized_tile_icon_without_alt_is_decorative_or_unknown(tmp_path):
    raw = '<html><body><div class="dialog_tree__tile_image"><img src="/i.png"></div></body></html>'
    assert _images(tmp_path, raw) == (1, 1)


def test_a_loading_indicator_without_alt_is_decorative_or_unknown(tmp_path):
    raw = '<html><body><div class="dialog_tree__preloader"><img src="/l.gif"></div></body></html>'
    assert _images(tmp_path, raw) == (1, 1)


def test_an_inline_svg_is_decorative_or_unknown(tmp_path):
    assert _images(tmp_path, "<html><body><svg width='20'><circle/></svg></body></html>") == (1, 1)


def test_an_image_inside_a_presentation_container_is_decorative_or_unknown(tmp_path):
    raw = '<html><body><div role="presentation"><img src="/p.jpg" width="300"></div></body></html>'
    assert _images(tmp_path, raw) == (1, 1)


def test_icons_the_snapshot_does_not_list_do_not_explain_the_photos_it_does(tmp_path):
    """The raw page holds everything, the snapshot only what is on screen: 30 footer tile
    icons cannot cover 8 content photos the snapshot lists."""
    aria = '- main:\n  - heading "X" [level=1]\n' + "  - img\n" * 8
    raw = ('<html><body><main>' + '<img src="/p.jpg" width="300" height="200">' * 8 + "</main>"
           '<footer><div class="icon-tile">' + '<img src="/i.png">' * 30 + "</div></footer></body></html>")
    assert _images(tmp_path, raw, aria) == (8, 0)


def test_only_what_the_plain_images_cannot_account_for_is_called_decorative(tmp_path):
    aria = '- main:\n  - heading "X" [level=1]\n' + "  - img\n" * 8
    raw = ('<html><body><main>' + '<img src="/p.jpg" width="300" height="200">' * 3 + "</main>"
           '<footer><div class="icon-tile">' + '<img src="/i.png">' * 30 + "</div></footer></body></html>")
    assert _images(tmp_path, raw, aria) == (8, 5)


def test_a_plain_content_image_without_alt_is_not_called_decorative(tmp_path):
    raw = '<html><body><div class="article"><img src="/p.jpg" width="300" height="200"></div></body></html>'
    assert _images(tmp_path, raw) == (1, 0)


def test_an_image_the_page_hides_does_not_explain_a_visible_one(tmp_path):
    raw = ('<html><body><div class="icon-box hidden"><img src="/i.png"></div>'
           '<div class="article"><img src="/p.jpg" width="300"></div></body></html>')
    assert _images(tmp_path, raw) == (1, 0)


def test_without_a_raw_capture_the_split_is_absent(tmp_path):
    images = measure_names(_store(tmp_path, aria="- main:\n  - heading \"X\" [level=1]\n  - img\n"))["images"]
    assert images["unnamed_content"] == 1 and images["unnamed_content_decorative"] == 0


# --- wording and cost ----------------------------------------------------------------

def _stats(findings, **measured):
    return {"accessibility": {"findings": findings, "measured": measured},
            "outcomes": {"participant_count": 1}}


def _finding(topic, category, severity="poważny"):
    return {"category": category, "severity": severity, "topic": topic, "verified": True}


def _a11y_lines(findings, **measured):
    return [d for d in compute_ux_score(_stats(findings, **measured))["deductions"]
            if d["kind"] == "accessibility"]


def test_the_category_names_are_their_own_topics_not_a_missing_name():
    assert a11y_topic("Zduplikowany link graficzny bez nazwy Na najgorszej stronie 4 linki") \
        == "redundant_link"
    assert a11y_topic("Baner zgody na pliki cookie: elementy bez nazwy") == "consent_notice"
    assert a11y_topic("Brak nazwy dostępnej dla linku") == "control_name"


def test_the_unnamed_control_line_is_labelled_for_what_it_covers():
    """No list of roles: which roles are unnamed varies (buttons most often), and a reader
    would look for the sliders and links the label names."""
    (line,) = _a11y_lines([_finding("control_name", "Brak nazwy dostępnej dla linku")])
    assert line["label"] == "Dostępność: kontrolki bez nazwy dostępnej"


def test_what_a_real_unnamed_control_costs_is_unchanged():
    (line,) = _a11y_lines([_finding("control_name", "Brak nazwy dostępnej dla linku")])
    assert line["amount"] == RULES["accessibility"]["poważny"] == 0.25
    (line,) = _a11y_lines([_finding("control_name", "X", "krytyczny")])
    assert line["amount"] == RULES["accessibility"]["krytyczny"]


def test_a_page_set_with_only_redundant_twins_has_no_serious_deduction():
    lines = _a11y_lines([_finding("redundant_link", "Zduplikowany link graficzny bez nazwy", "drobny")],
                        controls={"total": 10, "unnamed": 4, "unnamed_redundant_link": 4})
    assert [(d["label"], d["severity"], d["amount"]) for d in lines] == [
        ("Dostępność: Zduplikowany link graficzny bez nazwy", "drobny", 0.1)]


def test_the_serious_line_says_what_is_priced_apart():
    (line,) = _a11y_lines([_finding("control_name", "X")],
                          controls={"total": 50, "unnamed": 9, "unnamed_redundant_link": 6,
                                    "unnamed_consent": 1})
    assert "zduplikowanych linków graficznych: 6" in line["basis"]
    assert "elementów banera zgody: 1" in line["basis"]
    assert "wymienione osobno, bez kary" in line["basis"]


TWINS = _finding("redundant_link", "Zduplikowany link graficzny bez nazwy", "drobny")
CONSENT = _finding("consent_notice", "Baner zgody na pliki cookie: elementy bez nazwy", "drobny")
CONTROLS = {"controls": {"total": 50, "unnamed": 9, "unnamed_redundant_link": 6, "unnamed_consent": 1}}


def _total(*findings):
    return compute_ux_score(_stats(list(findings), **CONTROLS))["total_deduction"]


@pytest.mark.parametrize("apart", [TWINS, CONSENT, (TWINS, CONSENT)])
def test_items_priced_apart_do_not_add_to_the_unnamed_control_cost(apart):
    """The old line charged every unnamed control once, 0.25. Pricing the twins and the
    notice's bare controls on top of the controls that remain made the same data cost more."""
    apart = apart if isinstance(apart, tuple) else (apart,)
    control = _finding("control_name", "Brak nazwy dostępnej dla linku")
    assert _total(control, *apart) == _total(control) == RULES["accessibility"]["poważny"]
    lines = {d["label"]: d for d in _a11y_lines([control, *apart], **CONTROLS)}
    for f in apart:
        line = lines[f"Dostępność: {f['category']}"]
        assert line["amount"] == 0 and line["severity"] == "drobny"
        assert "bez osobnej kary" in line["basis"] and "kontrolki bez nazwy dostępnej" in line["basis"]


def test_the_twins_keep_their_minor_weight_when_no_unnamed_control_line_is_charged():
    assert _total(TWINS) == RULES["accessibility"]["drobny"]
    assert _total(TWINS, CONSENT) == 2 * RULES["accessibility"]["drobny"]
    unverified = {**_finding("control_name", "Brak nazwy dostępnej dla linku"), "verified": False}
    assert _total(unverified, TWINS) == RULES["accessibility"]["drobny"]


def test_the_alt_text_line_says_icons_and_backdrops_only_when_that_was_measured():
    images = {"total": 421, "unnamed": 13, "unnamed_content": 8, "share_unnamed_content": 0.019,
              "share_unnamed": 0.031}
    (line,) = _a11y_lines([_finding("alt_text", "Brak tekstu alternatywnego")],
                          images={**images, "unnamed_content_decorative": 8})
    assert line["label"] == "Dostępność: obrazy bez atrybutu alt (głównie ikony i tła)"
    assert line["severity"] == "drobny" and line["amount"] == RULES["accessibility"]["drobny"]
    (line,) = _a11y_lines([_finding("alt_text", "Brak tekstu alternatywnego")], images=images)
    assert line["label"] == "Dostępność: Brak tekstu alternatywnego", "no split measured: as the model wrote it"
    (line,) = _a11y_lines([_finding("alt_text", "Brak tekstu alternatywnego")],
                          images={**images, "unnamed_content_decorative": 1})
    assert line["label"] == "Dostępność: Brak tekstu alternatywnego", "not mostly icons"


def test_the_systemic_threshold_is_untouched():
    images = {"total": 100, "unnamed": 40, "unnamed_content": 40, "share_unnamed_content": 0.4,
              "share_unnamed": 0.4, "unnamed_content_decorative": 40}
    (line,) = _a11y_lines([_finding("alt_text", "Brak tekstu alternatywnego")], images=images)
    assert line["severity"] == "poważny" and line["amount"] == RULES["accessibility"]["poważny"]
    assert "głównie ikony" not in line["label"], "a serious finding is not worded as an icon note"


# --- wired into the audit, after the model ------------------------------------------

def test_the_audit_adds_the_code_decided_findings_after_the_models(tmp_path, monkeypatch):
    aria = ARIA + "  - slider\n"
    store = _store(tmp_path, aria=aria, raw=_page(IMAGE_TWIN))
    seen = {}

    async def chat(messages, **_kw):
        seen["prompt"] = messages[0]["content"]
        return {"overall_score": 3, "summary": "S.", "findings": [
            {"category": "Brak nazwy suwaka", "severity": "poważny", "detail": "d", "step": 0}]}

    monkeypatch.setattr(auditor, "_chat_json", chat)
    import asyncio
    result = asyncio.run(auditor._run_accessibility_summary(store))
    assert [f["category"] for f in result["findings"]] == [
        "Brak nazwy suwaka", "Zduplikowany link graficzny bez nazwy"]
    assert '"role": "link"' not in seen["prompt"] and '"role": "slider"' in seen["prompt"]


# --- the stored bnpparibas.pl run ----------------------------------------------------

STORED = Path(__file__).resolve().parents[1] / "runs" / "2026-11-10_16-15-42_cfe5"


@pytest.mark.skipif(not STORED.exists(), reason="the stored bnpparibas.pl run is not on this machine")
def test_the_stored_run_loses_its_links_and_keeps_its_sliders_and_fields(tmp_path):
    """Pinned loosely: 34 of 3730 controls were unnamed; 22 are image twins of named links,
    8 are sliders, lists and fields, and 4 are the teaser image links of one carousel that
    stay in tab order (a focusable link with no name is not redundant). 8 'content images'
    were icons, backdrops and graphics."""
    stores = []
    for src in sorted(p for p in STORED.iterdir() if p.is_dir()):
        dst = tmp_path / src.name
        dst.mkdir()
        for sub in ("a11y_snapshots", "raw_html"):
            shutil.copytree(src / sub, dst / sub)
        shutil.copy(src / "observation_trace.jsonl", dst / "observation_trace.jsonl")
        stores.append(TraceStore(dst))
    roles = set().union(*(_candidate_roles(s) for s in stores))
    assert {"slider", "combobox", "textbox"} <= roles
    link_pages = [c["pages_affected"] for s in stores for c in extract_a11y_candidates(s)["candidates"]
                  if c.get("role") == "link"]
    assert link_pages == [1], "only the carousel's in-tab-order image links remain unnamed links"
    measured = merge_pages([measure_names(s) for s in stores])
    controls, images = measured["controls"], measured["images"]
    assert controls["unnamed"] == 34 and controls["total"] == 3730 and images["total"] == 421
    assert controls["unnamed_redundant_link"] >= 20 and controls["unnamed_serious"] >= 6
    assert controls["unnamed_redundant_link"] + controls["unnamed_consent"] + controls["unnamed_serious"] == 34
    assert images["unnamed_content"] == 8 and images["unnamed_content_decorative"] == 8
    findings = [f for s in stores for f in extract_unnamed_link_findings(s)]
    assert findings and {f["topic"] for f in findings} == {"redundant_link"}
