"""What an issue shows as proof, and what several participants said that no one compared.

The 2026-10-01 hyundai report (a) put Joanna's configurator-summary screenshot under
"the booking form has no calendar" because every example of a heuristic is attached
to every issue of it, and (b) kept five participants' near-identical accounts of a
blocking overlay each in its own card, saying nowhere that they agreed.

(a) The narrator now names the examples that bear out what it wrote. (b) Elements
that took clicks in several sessions are counted from the trace, and accounts that
several participants gave are grouped by a model and kept only where each named
participant's own text contains the phrase."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document

from simulated_web_agent.main.obstacles import (
    OBSTACLES_INTRO, intercepting_element, obstacle_text, repeated_obstacles,
)
from simulated_web_agent.main.recurring import (
    group_recurring_reports, prompt_payload, validate_themes,
)
from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter import (
    _attach_issue_evidence, aggregate_statistics, generate_aggregated_report, resolve_narrative,
)
from simulated_web_agent.main.reporter_html import generate_html_report
from simulated_web_agent.main.trace_store import TraceStore
from tests.conftest import make_agent_report, make_finding

# ----------------------------------------------------------------------------------------
# (a) evidence ids
# ----------------------------------------------------------------------------------------

CALENDAR = {"id": "H7-e1", "persona_name": "Dorota", "step": 30, "url": "https://x.pl/jazda",
            "evidence": "Formularz jazdy testowej nie ma kalendarza terminów.",
            "screenshot": "d/screenshot/screenshot_30.png"}
LEASING = {"id": "H7-e2", "persona_name": "Joanna", "step": 13, "url": "https://x.pl/podsumowanie",
           "evidence": "Podsumowanie nie ma kalkulatora leasingu.",
           "screenshot": "j/screenshot/screenshot_13.png"}


def _issue(**kw):
    return {"heuristic_id": 7, "heuristic_name": "Elastyczność", "severity": "drobny",
            "frequency": "2 z 6", "impact": "Formularz nie ma kalendarza terminów.",
            "recommendation": "Dodać kalendarz.", **kw}


def _attach(issue):
    narrative = {"critical_issues": [], "serious_issues": [], "minor_issues": [issue]}
    stats = {"heuristic": {"by_heuristic": [{"heuristic_id": 7, "verified": True,
                                            "examples": [dict(CALENDAR), dict(LEASING)]}]},
             "accessibility": {}}
    _attach_issue_evidence(narrative, stats)
    return narrative["minor_issues"][0]


def test_the_issue_shows_only_the_examples_it_names():
    issue = _attach(_issue(evidence_ids=["H7-e1"]))
    assert [e["persona_name"] for e in issue["examples"]] == ["Dorota"]
    assert [o["persona_name"] for o in issue["other_observations"]] == ["Joanna"]
    assert "kalkulatora leasingu" in issue["other_observations"][0]["evidence"]
    assert "screenshot" not in issue["other_observations"][0], "no picture for a claim it does not prove"


def test_naming_both_shows_both_and_leaves_nothing_out():
    issue = _attach(_issue(evidence_ids=["H7-e1", "H7-e2"]))
    assert len(issue["examples"]) == 2 and issue["other_observations"] == []


@pytest.mark.parametrize("ids", [None, [], ["H7-e9"], ["H2-e1"], "H7-e1", [None, {}], 7])
def test_without_a_usable_id_every_example_is_shown_as_before(ids):
    issue = _attach(_issue() if ids is None else _issue(evidence_ids=ids))
    assert len(issue["examples"]) == 2 and issue["other_observations"] == []


def test_a_valid_id_among_junk_still_counts():
    issue = _attach(_issue(evidence_ids=[3, "", "H7-e2", "H7-e404"]))
    assert [e["persona_name"] for e in issue["examples"]] == ["Joanna"]


def test_attaching_twice_gives_the_same_answer():
    narrative = {"critical_issues": [], "serious_issues": [], "minor_issues": [_issue(evidence_ids=["H7-e1"])]}
    stats = {"heuristic": {"by_heuristic": [{"heuristic_id": 7, "verified": True,
                                            "examples": [dict(CALENDAR), dict(LEASING)]}]}, "accessibility": {}}
    for _ in range(2):
        _attach_issue_evidence(narrative, stats)
    issue = narrative["minor_issues"][0]
    assert len(issue["examples"]) == 1 and len(issue["other_observations"]) == 1


def test_resolve_narrative_applies_it_when_a_stored_report_is_rendered():
    agg = {"narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                         "serious_issues": [], "minor_issues": [_issue(evidence_ids=["H7-e2"])]},
           "statistics": {"heuristic": {"by_heuristic": [{"heuristic_id": 7, "verified": True,
                                                         "examples": [dict(CALENDAR), dict(LEASING)]}]}}}
    (issue,) = resolve_narrative(agg)["minor_issues"]
    assert [e["persona_name"] for e in issue["examples"]] == ["Joanna"]


def test_aggregation_gives_every_example_an_id_the_narrator_can_cite(tmp_path):
    from tests.test_heuristic_group_examples import _report
    a = {**make_finding(heuristic_id=7, severity="drobny", step=30), "evidence": "Brak kalendarza"}
    b = {**make_finding(heuristic_id=7, severity="drobny", step=13), "evidence": "Brak kalkulatora"}
    stats = aggregate_statistics([_report("Dorota", a), _report("Joanna", b)])
    group = next(h for h in stats["heuristic"]["by_heuristic"] if h["heuristic_id"] == 7)
    assert [e["id"] for e in group["examples"]] == ["H7-e1", "H7-e2"]


def _agg_with_other_observation():
    issue = _issue(evidence_ids=["H7-e1"])
    shown = _attach(issue)
    return {"website": "x.pl", "task": "t",
            "narrative": {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [],
                          "serious_issues": [], "minor_issues": [shown]},
            "statistics": {"participant_count": 2,
                           "heuristic": {"by_heuristic": [{"heuristic_id": 7, "verified": True,
                                                          "examples": [dict(CALENDAR), dict(LEASING)]}]}}}


def test_the_html_card_shows_one_proof_and_lists_the_other_observation_as_such():
    html = generate_html_report(_agg_with_other_observation())
    assert html.count('class="ev-label"') == 1 and "Dowód — uczestnik Dorota, krok 30" in html
    assert "Inne obserwacje w tej heurystyce" in html and "Joanna, krok 13" in html
    assert "bez zrzutu ekranu" in html


def test_the_word_file_says_the_same(tmp_path):
    doc = Document(build_docx(_agg_with_other_observation(), tmp_path, tmp_path / "r.docx"))
    text = " | ".join(p.text for p in doc.paragraphs)
    assert "Inne obserwacje w tej heurystyce" in text and "Joanna, krok 13" in text
    assert "Dorota · krok 30" in text, "the proof it names is still shown"


def test_the_prompt_tells_the_narrator_to_cite_and_the_schema_has_the_field():
    prompt = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent" / "audit_prompts"
              / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert '"evidence_ids"' in prompt and "`evidence_ids`" in prompt
    assert "fałszywym dowodem" in prompt


NARRATIVE = {"executive_summary": "S.", "overall_ux_score": 3, "key_strengths": [],
             "critical_issues": [], "serious_issues": [], "minor_issues": [],
             "priority_recommendations": [], "first_impression_assessment": "OK",
             "accessibility_assessment": "OK", "responsiveness_assessment": "OK", "methodology_note": "T"}


def _session(tmp_path, name, *, findings=(), reflection=None, observations=()):
    store = TraceStore(tmp_path / name)
    store.trace_dir.mkdir(parents=True, exist_ok=True)
    for obs in observations:
        store.append_observation(obs)
    if reflection:
        store.write_objective_reflection(reflection)
    report = make_agent_report(findings=list(findings))
    report["persona"] = f"Persona: {name}"
    report["trace_dir"] = str(store.trace_dir)
    store.write_ux_report(report)
    return store


@pytest.mark.asyncio
async def test_the_ids_reach_the_model_in_the_aggregate_prompt(tmp_path):
    f = {**make_finding(heuristic_id=7, severity="drobny", step=0), "evidence": "Brak kalendarza"}
    stores = [_session(tmp_path, "Dorota", findings=[f])]
    chat = AsyncMock(return_value=json.dumps(NARRATIVE))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        await generate_aggregated_report(stores=stores, task_description="t", website_url="https://x.pl")
    prompt = next(c.args[0][0]["content"] for c in chat.call_args_list if c.kwargs.get("model") == "report")
    assert '"id": "H7-e1"' in prompt


# ----------------------------------------------------------------------------------------
# (b1) elements that took clicks in several sessions
# ----------------------------------------------------------------------------------------

SPAN = ('Error executing action: Locator.click: Timeout 5000ms exceeded. Call log:\n'
        '  - waiting for locator("x")\n'
        '      - scrolling into view if needed\n'
        '      - <span data-v-3ad1f741="" data-v-31a40309="" class="s-color-preview" '
        'data-testselector="s-color-preview"></span> intercepts pointer events\n'
        '    - retrying click action')
BUTTON = ('Error executing action: Locator.click: Timeout 5000ms exceeded. Call log:\n'
          '      - <button disabled type="button" data-v-d2f94ecb="" aria-label="next slides" '
          'class="ods-icon-button ods-icon-button--variant-discrete ods-carousel__button">…</button> '
          'from <div data-v-731520f2="" class="ods-carousel__buttons-wrapper">…</div> subtree '
          'intercepts pointer events\n  - retrying click action')
BARE_DIV = ('Error executing action: Locator.click: Timeout 5000ms exceeded. Call log:\n'
            '      - <div data-v-1=""></div> intercepts pointer events')


@pytest.mark.parametrize("error, expected", [
    (SPAN, "span.s-color-preview"),
    (BUTTON, "button.ods-icon-button.ods-icon-button--variant-discrete"),
    ('- <section id="share-modal"></section> intercepts pointer events', "section#share-modal"),
    ('- <a aria-label="Zamknij"></a> intercepts pointer events', 'a[aria-label="Zamknij"]'),
    (BARE_DIV, None),                       # "a div" is not a named obstacle
    ("Timeout 5000ms exceeded", None), ("", None), (None, None), (42, None),
])
def test_the_element_that_took_the_click_is_named_by_its_classes(error, expected):
    assert intercepting_element(error) == expected


def _obs(error=None, url="https://www.hyundai.com/pl/pl/konfigurator.html?code=1#/color"):
    return {"tabs": [{"url": url, "is_active": True}], "error": error}


def _stores(tmp_path, per_session):
    out = []
    for i, errors in enumerate(per_session):
        s = TraceStore(tmp_path / f"s{i}")
        for e in errors:
            s.append_observation(_obs(e))
        out.append(s)
    return out


def test_an_element_that_took_clicks_in_two_sessions_is_reported_once(tmp_path):
    stores = _stores(tmp_path, [[None, SPAN, SPAN], [SPAN], [None]])
    (row,) = repeated_obstacles(stores, 3)
    assert row["element"] == "span.s-color-preview" and row["sessions"] == 2 and row["of"] == 3
    assert row["pages"] == ["www.hyundai.com/pl/pl/konfigurator.html#/color"]


def test_a_single_session_is_one_participants_bad_luck(tmp_path):
    assert repeated_obstacles(_stores(tmp_path, [[SPAN, SPAN, SPAN], [None], [BUTTON]]), 3) == []


def test_unrelated_obstacles_are_counted_apart_and_the_larger_group_comes_first(tmp_path):
    rows = repeated_obstacles(_stores(tmp_path, [[SPAN, BUTTON], [SPAN, BUTTON], [SPAN]]), 3)
    assert [(r["element"].split(".")[0], r["sessions"]) for r in rows] == [("span", 3), ("button", 2)]


def test_a_store_that_cannot_be_read_does_not_stop_the_report(tmp_path):
    class Broken:
        trace_dir = tmp_path / "broken"

        def read_observations(self):
            raise OSError("gone")

    assert repeated_obstacles([Broken(), *_stores(tmp_path, [[SPAN], [SPAN]])], 3)[0]["sessions"] == 2


def test_the_sentence_names_the_element_the_count_and_the_page(tmp_path):
    (row,) = repeated_obstacles(_stores(tmp_path, [[SPAN], [SPAN]]), 6)
    text = obstacle_text(row)
    assert "„span.s-color-preview” przejmował kliknięcia w 2 z 6 sesji" in text and "konfigurator.html" in text
    assert "nie dowodzi" in OBSTACLES_INTRO, "it does not claim a lost conversion"


def test_the_statistics_the_html_and_the_word_file_carry_them(tmp_path):
    stores = _stores(tmp_path, [[SPAN], [SPAN], [None]])
    reports = [{"trace_dir": str(s.trace_dir), "persona": f"Persona: P{i}", "first_impression": {},
                "heuristic_findings": [], "accessibility": {"findings": []}, "survey_result": {"answers": []}}
               for i, s in enumerate(stores)]
    stats = aggregate_statistics(reports, stores=stores)
    assert stats["repeated_obstacles"][0]["sessions"] == 2
    agg = {"website": "x.pl", "task": "t", "narrative": {"executive_summary": "S.", "overall_ux_score": 3,
           "critical_issues": [], "serious_issues": [], "minor_issues": []}, "statistics": stats}
    html = generate_html_report(agg)
    assert "Powtarzające się przeszkody w klikaniu" in html and "przejmował kliknięcia w 2 z 3 sesji" in html
    doc = Document(build_docx(agg, tmp_path, tmp_path / "r.docx"))
    assert "przejmował kliknięcia w 2 z 3 sesji" in " | ".join(p.text for p in doc.paragraphs)


def test_a_report_without_any_obstacle_has_no_such_section():
    agg = {"website": "x.pl", "task": "t", "narrative": {"executive_summary": "S.", "overall_ux_score": 3,
           "critical_issues": [], "serious_issues": [], "minor_issues": []},
           "statistics": {"participant_count": 2, "repeated_obstacles": [], "recurring_reports": []}}
    assert "Powtarzające się przeszkody" not in generate_html_report(agg)
    assert "Opisywane przez wielu uczestników" not in generate_html_report(agg)


# ----------------------------------------------------------------------------------------
# (b2) what several participants said, grouped by a model and checked against their own words
# ----------------------------------------------------------------------------------------

def _refl(name, *blockers, fix=""):
    return {"persona": name, "blockers": list(blockers), "priorityFix": fix, "summary": "Relacja."}


MODAL = [
    _refl("Tomasz", "Nakładka udostępniania (modal share) blokowała nawigację przy wychodzeniu z konfiguratora."),
    _refl("Agnieszka", "Zablokowanie interfejsu przez zacinające się okno udostępniania (share dialog)."),
    _refl("Dorota", "Wyskakujący modal blokujący kliknięcie przycisku jazdy próbnej."),
    _refl("Zuzanna", "Bardzo mały obszar klikalny (tylko checkbox) przy wyborze wersji."),
]


def _theme(label, *reports):
    return {"theme": label, "reports": [{"persona": p, "phrase": ph} for p, ph in reports]}


GOOD = _theme("Okno udostępniania blokujące klikanie w konfiguratorze",
              ("Tomasz", "Nakładka udostępniania (modal share)"),
              ("Agnieszka", "zacinające się okno udostępniania"),
              ("Dorota", "modal blokujący kliknięcie przycisku"))


def test_a_theme_every_named_participant_bears_out_is_kept():
    (theme,) = validate_themes({"themes": [GOOD]}, MODAL, 4)
    assert theme["participants"] == ["Tomasz", "Agnieszka", "Dorota"] and theme["of"] == 4
    assert theme["phrases"]["Dorota"] == "modal blokujący kliknięcie przycisku"


def test_a_phrase_the_participant_never_wrote_removes_that_participant():
    bad = _theme("Okno", ("Tomasz", "Nakładka udostępniania (modal share)"),
                 ("Agnieszka", "zacinające się okno udostępniania"),
                 ("Dorota", "okno które nigdy nie zostało napisane"))
    assert validate_themes({"themes": [bad]}, MODAL, 4) == [], "two left: a coincidence, not a pattern"


def test_a_phrase_taken_from_someone_elses_text_does_not_count_for_the_named_participant():
    swapped = _theme("Okno", ("Tomasz", "Nakładka udostępniania (modal share)"),
                     ("Agnieszka", "zacinające się okno udostępniania"),
                     ("Dorota", "Nakładka udostępniania (modal share)"))
    assert validate_themes({"themes": [swapped]}, MODAL, 4) == []


def test_an_invented_participant_a_duplicate_and_a_tiny_phrase_are_not_participants():
    t = _theme("Okno", ("Tomasz", "Nakładka udostępniania (modal share)"),
               ("Tomasz", "Nakładka udostępniania (modal share)"),
               ("Nieistniejąca", "zacinające się okno udostępniania"),
               ("Dorota", "modal"))
    assert validate_themes({"themes": [t]}, MODAL, 4) == []


@pytest.mark.parametrize("raw", [None, [], "x", {}, {"themes": None}, {"themes": ["x", 3, None]},
                                 {"themes": [{"theme": "t", "reports": "x"}]}, {"themes": [{"reports": []}]}])
def test_whatever_the_model_returns_validation_does_not_raise(raw):
    assert validate_themes(raw, MODAL, 4) == []


def test_whitespace_and_case_do_not_defeat_a_genuine_quotation():
    t = _theme("Okno", ("Tomasz", "NAKŁADKA   udostępniania (modal share)"),
               ("Agnieszka", "zacinające się okno udostępniania"), ("Dorota", "modal blokujący kliknięcie"))
    assert len(validate_themes({"themes": [t]}, MODAL, 4)) == 1


def test_at_most_four_themes_and_each_is_cleaned_of_the_tools_vocabulary():
    themes = [_theme(f"Zjawisko {i} (mobile_overflow)", ("Tomasz", "Nakładka udostępniania (modal share)"),
                     ("Agnieszka", "zacinające się okno udostępniania"), ("Dorota", "modal blokujący kliknięcie"))
              for i in range(6)]
    out = validate_themes({"themes": themes}, MODAL, 4)
    assert len(out) == 4 and "mobile_overflow" not in out[0]["theme"]


def test_the_model_sees_what_participants_wrote_about_difficulty_and_nothing_else():
    payload = prompt_payload(MODAL + [{"persona": "Ewa", "blockers": [], "wins": ["Świetne menu"]}])
    assert [p["persona"] for p in payload] == ["Tomasz", "Agnieszka", "Dorota", "Zuzanna"], "no blockers, no row"
    assert set(payload[0]) == {"persona", "trudnosci", "najwazniejsza_poprawka"}


@pytest.mark.asyncio
async def test_too_few_accounts_do_not_cost_a_model_call():
    chat = AsyncMock()
    assert await group_recurring_reports(MODAL[:2], 6, chat) == []
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_a_reply_that_is_not_json_gives_no_themes():
    assert await group_recurring_reports(MODAL, 4, AsyncMock(return_value="nie json")) == []


@pytest.mark.asyncio
async def test_a_good_reply_is_validated_and_returned():
    chat = AsyncMock(return_value=json.dumps({"themes": [GOOD]}))
    (theme,) = await group_recurring_reports(MODAL, 4, chat)
    assert len(theme["participants"]) == 3
    sent = chat.call_args.args[0]
    assert "NIEZALEŻNIE" in sent[0]["content"] and "Tomasz" in sent[1]["content"]
    assert chat.call_args.kwargs.get("json_mode") is True


@pytest.mark.asyncio
async def test_the_report_carries_the_validated_themes_and_survives_a_failing_call(tmp_path):
    stores = [_session(tmp_path, r["persona"], reflection={**r, "outcome": "success", "wins": []})
              for r in MODAL]

    async def fake(messages, **kw):
        if kw.get("model") == "report":
            return json.dumps(NARRATIVE)
        if kw.get("json_mode") and "NIEZALEŻNIE" in messages[0]["content"]:
            return json.dumps({"themes": [GOOD]})
        raise RuntimeError("no other call in this test")

    with patch("simulated_web_agent.main.reporter.async_chat", new=fake):
        result = await generate_aggregated_report(stores=stores, task_description="t",
                                                 website_url="https://x.pl")
    (theme,) = result["statistics"]["recurring_reports"]
    assert theme["participants"] == ["Tomasz", "Agnieszka", "Dorota"]

    async def failing(messages, **kw):
        if kw.get("model") == "report":
            return json.dumps(NARRATIVE)
        if "NIEZALEŻNIE" in messages[0]["content"]:
            raise RuntimeError("the model is down")
        return json.dumps({"summaries": {}})

    with patch("simulated_web_agent.main.reporter.async_chat", new=failing):
        result = await generate_aggregated_report(stores=stores, task_description="t",
                                                 website_url="https://x.pl")
    assert result["statistics"]["recurring_reports"] == [] and result["narrative"]["executive_summary"]


def test_the_themes_appear_in_the_html_and_the_word_file_with_their_caveat(tmp_path):
    (theme,) = validate_themes({"themes": [GOOD]}, MODAL, 4)
    agg = {"website": "x.pl", "task": "t", "narrative": {"executive_summary": "S.", "overall_ux_score": 3,
           "critical_issues": [], "serious_issues": [], "minor_issues": []},
           "statistics": {"participant_count": 4, "recurring_reports": [theme]}}
    html = generate_html_report(agg)
    assert "Opisywane przez wielu uczestników" in html
    assert "3 z 4 uczestników" in html and "Agnieszka: „zacinające się okno udostępniania”" in html
    assert "To relacje, nie pomiar" in html
    doc = Document(build_docx(agg, tmp_path, tmp_path / "r.docx"))
    text = " | ".join(p.text for p in doc.paragraphs)
    assert "Opisywane przez wielu uczestników" in text and "3 z 4 uczestników" in text
