"""A pass that failed is not a pass that found nothing — and the narrator is told.

The pepco run (3 personas): the accessibility audit failed for all three (two
429s, one DNS error), PageSpeed answered "The read operation timed out" for both
strategies, one survey failed. The narrative still wrote an accessibility
assessment, put "przekroczenie czasu ładowania … w testach PageSpeed" at #2 of
the recommendations, and said "poważne problemy z wydajnością" in the summary:
the prompt asked it not to, and nothing checked. The checks are in code now.
"""
import copy
import json
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document

from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter import (
    _aggregate_links, _narrative_from_stats, _viewport_for_prompt, aggregate_statistics,
    generate_aggregated_report, guard_unmeasured, measurement_status, measurement_status_text, resolve_narrative,
)
from simulated_web_agent.main.reporter_html import generate_html_report
from simulated_web_agent.main.trace_store import TraceStore
from tests.conftest import make_agent_report, make_finding

PS_FAILED = {"url": "https://x.pl/", "mobile": {"error": "The read operation timed out"},
             "desktop": {"error": "The read operation timed out"}}
PS_OK = {"url": "https://x.pl/", "mobile": {"performance_score": 20}, "desktop": {"performance_score": 70}}

ALL = ["heuristic_evaluation", "first_impression", "accessibility"]


def _stats(**over):
    base = {
        "participant_count": 3,
        "audit_failures": [{"persona": f"P{i}", "persona_dir": f"p{i}", "passes": ["analiza dostępności"],
                            "pass_ids": ["accessibility"]} for i in range(3)],
        "accessibility": {"avg_score": None, "scored": 0, "of": 3},
        "first_impression": {"avg_score": 3.4, "scored": 3, "of": 3},
        "survey": {"failed_surveys": [{"persona_dir": "p1", "persona": "P1"}]},
        "viewport": {"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0, "devices": [
            {"viewport": "mobile", "pages_scanned": 3}, {"viewport": "tablet", "pages_scanned": 3}]},
    }
    base.update(over)
    return base


def _narr(**over):
    base = {
        "executive_summary": "Badanie ujawniło problem ze sklepami. Zidentyfikowano również poważne "
                             "problemy z wydajnością strony.",
        "key_strengths": ["Czytelna nawigacja", "Strona ładuje się szybko, wynik PageSpeed jest dobry."],
        "accessibility_assessment": "Strona jest dostępna dla osób z niepełnosprawnościami.",
        "first_impression_assessment": "Pierwsze wrażenie jest dobre.",
        "responsiveness_assessment": "Strona działa poprawnie na tabletach.",
        "priority_recommendations": [
            {"priority": 1, "area": "Lokalizator sklepów", "action": "Naprawić wyszukiwarkę sklepów.",
             "expected_impact": "Ukończenie zadania."},
            {"priority": 2, "area": "Wydajność serwisu",
             "action": "Przeprowadzić audyt przyczyn przekroczenia czasu ładowania strony (timeout) "
                       "w testach PageSpeed.",
             "expected_impact": "Znacząca poprawa doświadczenia."},
        ],
        "methodology_note": "Badanie przeprowadzono przy użyciu agentów LLM.",
    }
    base.update(over)
    return base


# --- the status, from the ledger -----------------------------------------------

def test_status_is_read_from_the_ledger_by_pass_id():
    status = measurement_status(_stats(), PS_FAILED, {"pages_failed": 1, "pages_total": 4})
    assert status["accessibility"] == {"failed": 3, "of": 3, "scored": 0}
    assert status["first_impression"]["failed"] == 0
    assert status["survey"] == {"failed": 1, "of": 3}
    assert status["viewport"]["missing"] == []
    assert status["links"] == {"pages_failed": 1, "pages_total": 4}
    assert status["pagespeed"] == {"mobile": "failed", "desktop": "failed"}


def test_status_does_not_match_on_polish_labels():
    """A row stored before pass_ids existed carries labels only: no failure is
    inferred from them, so a reworded label can never switch the guard on or off."""
    old = _stats(audit_failures=[{"persona": "P", "passes": ["analiza dostępności"]}])
    assert measurement_status(old, None)["accessibility"]["failed"] == 0


def test_pagespeed_states():
    assert measurement_status(_stats(), None)["pagespeed"] == {"mobile": "missing", "desktop": "missing"}
    assert measurement_status(_stats(), {"skipped": True})["pagespeed"]["mobile"] == "skipped"
    assert measurement_status(_stats(), PS_OK)["pagespeed"] == {"mobile": "ok", "desktop": "ok"}
    empty = {"url": "x", "mobile": {"performance_score": None}, "desktop": {}}
    assert measurement_status(_stats(), empty)["pagespeed"] == {"mobile": "failed", "desktop": "failed"}


def test_the_prompt_block_names_every_unmeasured_area_and_calls_it_a_tool_fault():
    text = measurement_status_text(measurement_status(
        _stats(), PS_FAILED, {"pages_failed": 1, "pages_total": 4}))
    assert "NIE ZMIERZONO / NIE POWIODŁO SIĘ" in text and "nie wada strony" in text
    assert "analiza dostępności: błąd narzędzia dla 3 z 3 uczestników" in text
    assert "ankieta: błąd narzędzia dla 1 z 3 uczestników" in text
    assert "PageSpeed mobile i desktop: pomiar nie powiódł się" in text
    assert "test linków" in text and "stron: 1 z 4" in text
    assert "test pierwszego wrażenia" not in text


def test_a_partly_failed_scan_is_named_in_the_prompt_block():
    stats = _stats(viewport={"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0, "devices": [
        {"viewport": "mobile", "pages_scanned": 3}, {"viewport": "tablet", "pages_scanned": 3}],
        "scan_errors": {"pages_failed": 2, "pages_total": 6}})
    text = measurement_status_text(measurement_status(stats, PS_OK))
    assert "responsywność: część stron na danym ekranie nie została przeskanowana" in text
    assert "(stron na ekranie: 2 z 6)" in text


def test_when_everything_measured_the_block_says_so():
    ok = _stats(audit_failures=[], survey={})
    assert measurement_status_text(measurement_status(ok, PS_OK)) == "(brak — każda analiza dała wynik)"


def test_a_run_without_a_ledger_reports_no_failure():
    legacy = {"participant_count": 3, "accessibility": {"avg_score": None}}
    assert measurement_status_text(measurement_status(legacy, None)) == "(brak — każda analiza dała wynik)"


# --- the guard, on the free-text fields -----------------------------------------

def test_an_assessment_of_a_pass_that_failed_for_everyone_is_replaced():
    narr = guard_unmeasured(_narr(), _stats(), PS_OK)
    assert narr["accessibility_assessment"].startswith("Analiza dostępności nie dała oceny")
    assert "3 z 3 uczestników" in narr["accessibility_assessment"]
    assert "usterka narzędzia" in narr["accessibility_assessment"]
    assert "jest dostępna" not in narr["accessibility_assessment"]
    assert narr["first_impression_assessment"] == "Pierwsze wrażenie jest dobre.", "it did run"


def test_a_partial_failure_keeps_the_text_and_says_whom_it_covers():
    stats = _stats(audit_failures=[{"persona": "P0", "passes": ["x"], "pass_ids": ["first_impression"]}],
                   first_impression={"avg_score": 3.4, "scored": 2, "of": 3})
    narr = guard_unmeasured(_narr(), stats, PS_OK)
    assert narr["first_impression_assessment"].startswith("Pierwsze wrażenie jest dobre.")
    assert "Ocena pierwszego wrażenia obejmuje 2 z 3 uczestników" in narr["first_impression_assessment"]


def test_an_unscored_pass_is_replaced_even_when_only_some_failed():
    """One failed and the others had nothing to scan: nobody has a score, and the
    narrator's text rests on nothing."""
    stats = _stats(audit_failures=[{"persona": "P0", "passes": ["x"], "pass_ids": ["accessibility"]}],
                   accessibility={"avg_score": None, "scored": 0, "of": 3})
    narr = guard_unmeasured(_narr(), stats, PS_OK)
    assert narr["accessibility_assessment"].startswith("Analiza dostępności nie dała oceny")
    assert "1 z 3" in narr["accessibility_assessment"]


def test_the_guard_can_run_on_its_own_output():
    once = guard_unmeasured(_narr(), _stats(), PS_FAILED, None)
    twice = guard_unmeasured(copy.deepcopy(once), _stats(), PS_FAILED, None)
    assert once == twice
    partial = _stats(audit_failures=[{"persona": "P0", "passes": ["x"], "pass_ids": ["accessibility"]}],
                     accessibility={"avg_score": 3, "scored": 2, "of": 3})
    p1 = guard_unmeasured(_narr(), partial, PS_OK)
    assert guard_unmeasured(copy.deepcopy(p1), partial, PS_OK) == p1


def test_a_performance_recommendation_becomes_repeat_the_measurement():
    narr = guard_unmeasured(_narr(), _stats(), PS_FAILED)
    first, second = narr["priority_recommendations"]
    assert first["action"] == "Naprawić wyszukiwarkę sklepów.", "untouched"
    assert second["priority"] == 2, "rewritten in place, not deleted"
    assert second["action"].startswith("Powtórzyć pomiar PageSpeed")
    assert "nie wada strony" in second["action"]
    assert second["expected_impact"] == ""
    assert "nie ma dowodu na problem z wydajnością" in second["measurement_caveat"]


def test_the_summary_and_strengths_lose_their_performance_claims_and_say_so_once():
    narr = guard_unmeasured(_narr(), _stats(), PS_FAILED)
    assert narr["executive_summary"] == "Badanie ujawniło problem ze sklepami."
    assert narr["key_strengths"] == ["Czytelna nawigacja"]
    assert narr["methodology_note"].count("Pomiar PageSpeed nie powiódł się") == 1
    again = guard_unmeasured(copy.deepcopy(narr), _stats(), PS_FAILED)
    assert again["methodology_note"].count("Pomiar PageSpeed nie powiódł się") == 1


def test_a_measured_performance_score_leaves_every_claim_alone():
    """Compare tests/test_wcag_evidence.py: mobile 20 is a real, poor result."""
    before = _narr()
    narr = guard_unmeasured(copy.deepcopy(before), _stats(), PS_OK)
    assert narr["priority_recommendations"] == before["priority_recommendations"]
    assert narr["executive_summary"] == before["executive_summary"]
    assert narr["key_strengths"] == before["key_strengths"]


def test_a_desktop_score_alone_is_enough_to_keep_the_claims():
    ps = {"url": "x", "mobile": {"error": "timed out"}, "desktop": {"performance_score": 38}}
    before = _narr()
    narr = guard_unmeasured(copy.deepcopy(before), _stats(), ps)
    assert narr["priority_recommendations"] == before["priority_recommendations"]


@pytest.mark.parametrize("ps", [None, {"url": "x", "skipped": True, "reason": "no_api_key"}])
def test_no_pagespeed_result_is_unmeasured_but_not_called_a_failure(ps):
    """No key, an unreadable pagespeed.json: the narrator was told "pomiar niedostępny",
    compute_ux_score has no performance line, and an issue about speed is set aside —
    the recommendation and the summary must not stand on nothing either. But nothing
    "nie powiódł się": no measurement was made."""
    narr = guard_unmeasured(_narr(), _stats(), ps)
    second = narr["priority_recommendations"][1]
    assert second["action"].startswith("Wykonać pomiar PageSpeed") and "nie wada strony" in second["action"]
    assert "nie powiódł się" not in second["action"] and "nie ma wyniku pomiaru" in second["measurement_caveat"]
    assert narr["executive_summary"] == "Badanie ujawniło problem ze sklepami."
    assert narr["key_strengths"] == ["Czytelna nawigacja"]
    assert "Pomiaru PageSpeed nie ma" in narr["methodology_note"]
    assert "nie powiódł się" not in narr["methodology_note"]
    again = guard_unmeasured(copy.deepcopy(narr), _stats(), ps)
    assert again == narr


def test_a_recommendation_about_something_else_is_never_rewritten():
    before = _narr()
    guard_unmeasured(before, _stats(), PS_FAILED)
    assert before["priority_recommendations"][0]["action"] == "Naprawić wyszukiwarkę sklepów."


def test_a_recommendation_that_names_only_the_timeout_is_rewritten_too():
    """The narrator does not always write "PageSpeed" beside the timeout it read as a defect."""
    only_timeout = [{"priority": 1, "area": "Serwis", "expected_impact": "Szybsza strona.",
                     "action": "Wyeliminować przekroczenia limitu czasu odpowiedzi."}]
    narr = guard_unmeasured(_narr(priority_recommendations=copy.deepcopy(only_timeout)), _stats(), PS_FAILED)
    assert narr["priority_recommendations"][0]["action"].startswith("Powtórzyć pomiar PageSpeed")
    kept = guard_unmeasured(_narr(priority_recommendations=copy.deepcopy(only_timeout)), _stats(), PS_OK)
    assert kept["priority_recommendations"] == only_timeout, "a measured run is left alone"


def test_responsiveness_nobody_measured_is_replaced():
    stats = _stats(viewport={"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0, "devices": []})
    narr = guard_unmeasured(_narr(), stats, PS_OK)
    assert narr["responsiveness_assessment"].startswith("Nie zmierzono responsywności")
    assert "poprawnie" not in narr["responsiveness_assessment"]


def test_responsiveness_one_width_unmeasured_is_named_beside_the_rest():
    stats = _stats(viewport={"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0,
                             "devices": [{"viewport": "mobile", "pages_scanned": 3}]})
    narr = guard_unmeasured(_narr(), stats, PS_OK)
    assert narr["responsiveness_assessment"].startswith("Strona działa poprawnie na tabletach.")
    assert "Nie zmierzono responsywności na: tablet" in narr["responsiveness_assessment"]


def test_a_report_stored_without_a_ledger_or_device_list_is_not_rewritten():
    legacy = {"participant_count": 3, "accessibility": {"avg_score": None},
              "viewport": {"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 3}}
    before = _narr()
    narr = guard_unmeasured(copy.deepcopy(before), legacy, PS_OK)
    assert narr == before


# --- viewport and link-check failures reach the narrator ------------------------

def test_a_failed_scan_goes_to_the_narrator_as_null_not_zero():
    vp = {"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0, "devices": [],
          "scan_errors": {"pages_failed": 4, "pages_total": 4}}
    out = _viewport_for_prompt(vp)
    assert out["mobile_overflow_sessions"] is None and out["tablet_overflow_sessions"] is None
    assert "niedostępny" in out["pomiar"] and "nie brak problemów" in out["pomiar"]
    assert vp["mobile_overflow_sessions"] == 0, "the stored integers are not touched"


def test_only_the_unscanned_width_is_nulled():
    vp = {"mobile_overflow_sessions": 2, "tablet_overflow_sessions": 0,
          "devices": [{"viewport": "mobile", "pages_scanned": 3}]}
    out = _viewport_for_prompt(vp)
    assert out["mobile_overflow_sessions"] == 2 and out["tablet_overflow_sessions"] is None
    assert "tablet" in out["pomiar"] and "mobile" not in out["pomiar"]


def test_a_fully_scanned_viewport_and_a_legacy_one_pass_through():
    full = {"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0,
            "devices": [{"viewport": "mobile", "pages_scanned": 1},
                        {"viewport": "tablet", "pages_scanned": 1}]}
    assert _viewport_for_prompt(full) == full
    legacy = {"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 3}
    assert _viewport_for_prompt(legacy) == legacy


def test_the_automatic_summary_does_not_print_zero_for_a_scan_that_measured_nothing():
    """The fallback narrative used when the model call failed: "0/3" read as
    "no overflow in 3 sessions" over a scan that never ran."""
    nothing = _narrative_from_stats({"participant_count": 3, "heuristic": {},
                                     "viewport": {"mobile_overflow_sessions": 0,
                                                  "tablet_overflow_sessions": 0, "devices": []}})
    assert nothing["responsiveness_assessment"].count("nie zmierzono") == 2
    assert "0/3" not in nothing["responsiveness_assessment"]
    legacy = _narrative_from_stats({"participant_count": 3, "heuristic": {},
                                    "viewport": {"mobile_overflow_sessions": 1,
                                                 "tablet_overflow_sessions": 0}})
    assert "mobilnym: 1/3" in legacy["responsiveness_assessment"], "no device list: printed as before"


def _report_with_scans(scans):
    r = make_agent_report()
    r["viewport_findings"] = scans
    return r


def test_aggregate_statistics_counts_failed_scans_and_keeps_the_integers():
    err = lambda url, vp: {"url": url, "viewport": vp, "error": "net::ERR", "horizontal_overflow": None}
    ok = lambda url, vp: {"url": url, "viewport": vp, "error": None, "horizontal_overflow": False}
    stats = aggregate_statistics([_report_with_scans([ok("https://x.pl/", "mobile"),
                                                      err("https://x.pl/a", "mobile")])])
    vp = stats["viewport"]
    assert vp["scan_errors"] == {"pages_failed": 1, "pages_total": 2}
    assert vp["measured"] is True and isinstance(vp["mobile_overflow_sessions"], int)
    nothing = aggregate_statistics([_report_with_scans([err("https://x.pl/", "mobile"),
                                                        err("https://x.pl/", "tablet")])])["viewport"]
    assert nothing["scan_errors"] == {"pages_failed": 2, "pages_total": 2}
    assert nothing["measured"] is False and nothing["devices"] == []
    assert nothing["mobile_overflow_sessions"] == 0, "stored as an integer; only the prompt copy is null"


def test_a_page_that_one_participant_could_scan_is_not_a_failed_page():
    err = {"url": "https://x.pl/a", "viewport": "mobile", "error": "timeout"}
    ok = {"url": "https://x.pl/a", "viewport": "mobile", "error": None, "horizontal_overflow": False}
    stats = aggregate_statistics([_report_with_scans([err]), _report_with_scans([ok])])
    assert stats["viewport"]["scan_errors"] == {"pages_failed": 0, "pages_total": 1}


class _Store:
    def __init__(self, pages):
        self._p = pages

    def read_link_findings(self):
        return self._p


def test_a_page_the_link_check_could_not_open_is_never_a_clean_result():
    """"No broken links detected" over a page that was never opened."""
    failed = {"url": "https://x.pl/b", "links_checked": 0, "broken": [], "error": "net::ERR_ABORTED"}
    worked = {"url": "https://x.pl/a", "links_checked": 2, "broken": [],
              "checked_urls": ["https://x.pl/k", "https://x.pl/r"]}
    data, text = _aggregate_links([_Store([worked, failed])])
    assert (data["pages_failed"], data["pages_total"]) == (1, 2)
    assert "UWAGA" in text and "stron: 1 z 2" in text and "usterka narzędzia" in text
    assert "No broken links detected" in text, "what was checked is still reported"


def test_every_page_failing_is_not_called_a_clean_check():
    failed = {"url": "https://x.pl/b", "links_checked": 0, "broken": [], "error": "net::ERR_ABORTED"}
    data, text = _aggregate_links([_Store([failed]), _Store([failed])])
    assert (data["pages_failed"], data["pages_total"]) == (1, 1), "the same page, counted once"
    assert "nie powiódł się" in text and "nie jest wynik" in text
    assert "No broken links" not in text


def test_links_without_failures_read_as_before():
    page = {"url": "https://x.pl/a", "links_checked": 1, "broken": [], "checked_urls": ["https://x.pl/k"]}
    data, text = _aggregate_links([_Store([page])])
    assert data["pages_failed"] == 0
    assert text == "No broken links detected across 1 links checked."


def test_unchecked_links_are_told_to_the_narrator_too():
    page = {"url": "https://x.pl/a", "links_checked": 1, "broken": [], "checked_urls": ["https://x.pl/k"],
            "unchecked": [{"url": "https://x.pl/slow", "error": "timeout"}]}
    _, text = _aggregate_links([_Store([page])])
    assert "Nie udało się sprawdzić 1 linków" in text


# --- end to end, shaped like the real run ---------------------------------------

LLM_NARRATIVE = {
    "executive_summary": "Badanie z udziałem 3 uczestników ujawniło problemy ze sklepami. "
                         "Zidentyfikowano również poważne problemy z wydajnością strony oraz braki.",
    "overall_ux_score": 1,
    "key_strengths": ["Czytelna nawigacja"],
    "critical_issues": [], "serious_issues": [],
    "minor_issues": [
        {"heuristic_id": None, "heuristic_name": "Wydajność serwisu",
         "impact": "Przekroczenie czasu ładowania w teście PageSpeed.",
         "recommendation": "Zdiagnozować wydajność.", "frequency": "3 z 3"}],
    "accessibility_assessment": "Strona wykazuje poważne bariery dla osób korzystających z technologii asystujących.",
    "responsiveness_assessment": "Strona działa poprawnie na urządzeniach mobilnych i tabletach.",
    "first_impression_assessment": "Pierwsze wrażenie jest umiarkowanie pozytywne.",
    "priority_recommendations": [
        {"priority": 1, "area": "Lokalizator sklepów", "action": "Naprawić wyszukiwarkę sklepów.",
         "expected_impact": "Ukończenie zadania."},
        {"priority": 2, "area": "Wydajność serwisu",
         "action": "Wyeliminować przyczyny przekroczenia czasu ładowania strony (timeout) w testach PageSpeed.",
         "expected_impact": "Poprawa SEO."}],
    "methodology_note": "Badanie przeprowadzono przy użyciu agentów LLM.",
}


def _real_run(tmp_path):
    """3 personas: accessibility failed for all, one survey failed, PageSpeed and
    every viewport scan and link-check page errored."""
    stores = []
    for i in range(3):
        s = TraceStore(tmp_path / f"p{i}")
        report = make_agent_report(findings=[make_finding(heuristic_id=1, severity="poważny")])
        report["trace_dir"] = str(s.trace_dir)
        report["accessibility"] = {"error": "429 RESOURCE_EXHAUSTED"}
        report["viewport_findings"] = [
            {"url": "https://x.pl/", "viewport": vp, "error": "net::ERR_ABORTED",
             "horizontal_overflow": None} for vp in ("mobile", "tablet", "desktop")]
        if i == 1:
            report["survey_result"] = {"error": "boom"}
            s.write_survey_result({"error": "boom"})
        s.write_ux_report(report)
        s.write_viewport_findings(report["viewport_findings"])
        s.write_audit_status({"heuristic_evaluation": None, "first_impression": None,
                              "accessibility": "429 RESOURCE_EXHAUSTED"})
        s.write_link_findings([{"url": "https://x.pl/", "links_checked": 0, "broken": [],
                                "error": "net::ERR_ABORTED"}])
        stores.append(s)
    (tmp_path / "pagespeed.json").write_text(json.dumps(PS_FAILED), encoding="utf-8")
    return stores


@pytest.fixture
def real_run(tmp_path):
    stores = _real_run(tmp_path)

    async def go():
        chat = AsyncMock(return_value=json.dumps(LLM_NARRATIVE))
        with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
            agg = await generate_aggregated_report(stores, "Skompletuj dekoracje", "https://x.pl/")
        prompt = next(c.args[0][0]["content"] for c in chat.call_args_list
                      if c.kwargs.get("model") == "report" and c.kwargs.get("log") is False)
        return agg, prompt

    return tmp_path, go


@pytest.mark.asyncio
async def test_the_narrator_is_told_which_analyses_have_no_result(real_run):
    _, go = real_run
    agg, prompt = await go()
    assert "NIE ZMIERZONO / NIE POWIODŁO SIĘ" in prompt
    assert "analiza dostępności: błąd narzędzia dla 3 z 3 uczestników" in prompt
    assert "ankieta: błąd narzędzia dla 1 z 3 uczestników" in prompt
    assert "PageSpeed mobile i desktop: pomiar nie powiódł się" in prompt
    assert "responsywność (mobile, tablet)" in prompt
    assert "test linków: część stron nie została sprawdzona" in prompt
    # …and the data beside it does not look like a clean result
    assert '"mobile_overflow_sessions": null' in prompt and '"tablet_overflow_sessions": null' in prompt
    assert "pomiar niedostępny" in prompt, "PageSpeed block"
    assert "usterka narzędzia: linki nie zostały sprawdzone" in prompt
    assert "(Page speed data not available)" not in prompt
    # the ledger is also in the stored statistics, with stable ids
    rows = agg["statistics"]["audit_failures"]
    assert [r["pass_ids"] for r in rows] == [
        ["accessibility", "viewport_scan", "link_check"],
        ["accessibility", "survey", "viewport_scan", "link_check"],
        ["accessibility", "viewport_scan", "link_check"]]


@pytest.mark.asyncio
async def test_the_narratives_claims_about_failed_passes_do_not_survive(real_run):
    _, go = real_run
    agg, _ = await go()
    narr = agg["narrative"]
    assert narr["accessibility_assessment"].startswith("Analiza dostępności nie dała oceny")
    assert narr["responsiveness_assessment"].startswith("Nie zmierzono responsywności")
    assert narr["first_impression_assessment"] == LLM_NARRATIVE["first_impression_assessment"]
    second = narr["priority_recommendations"][1]
    assert second["action"].startswith("Powtórzyć pomiar PageSpeed")
    assert "wydajnością" not in narr["executive_summary"]
    assert "Pomiar PageSpeed nie powiódł się" in narr["methodology_note"]
    # a measurement fault is not an issue the report counts
    assert narr["minor_issues"] == []
    assert any(i.get("heuristic_name") == "Wydajność serwisu" for i in narr["unconfirmed_issues"])


@pytest.mark.asyncio
async def test_neither_format_presents_a_failed_pass_as_a_measurement(real_run, tmp_path):
    run_dir, go = real_run
    agg, _ = await go()
    html = generate_html_report(agg)
    out = Document(build_docx(agg, run_dir, run_dir / "r.docx"))
    docx = "\n".join(p.text for p in out.paragraphs) + "\n" + "\n".join(
        c.text for t in out.tables for row in t.rows for c in row.cells)
    for name, text in (("html", html), ("docx", docx)):
        assert "bariery dla osób korzystających z technologii asystujących" not in text, name
        assert "Strona działa poprawnie na urządzeniach mobilnych" not in text, name
        assert "przekroczenia czasu ładowania" not in text, name
        assert "wydajnością strony" not in text, name
        assert "Analiza dostępności nie dała oceny" in text, name
        assert "nie oceniono" in text.lower(), name
        assert "Powtórzyć pomiar PageSpeed" in text, name
        assert "nie ma dowodu na problem z wydajnością" in text, name
        assert "Ankieta nie powiodła się dla" in text, name
        assert "Nie wszystkie analizy się powiodły" in text, name
    assert "None/5" not in html and "—/5" not in html


@pytest.mark.asyncio
async def test_the_score_breakdown_names_the_failed_accessibility_pass(real_run):
    _, go = real_run
    agg, _ = await go()
    left_out = agg["statistics"]["score_breakdown"]["not_counted"]
    a11y = next(n for n in left_out if n["label"] == "Analiza dostępności")
    assert "3 z 3 uczestników" in a11y["reason"] and "usterka narzędzia" in a11y["reason"]
    assert any(n["label"] == "Wydajność strony" for n in left_out)


@pytest.mark.asyncio
async def test_a_stored_run_is_corrected_when_it_is_rendered(real_run):
    """resolve_narrative on the stored aggregate reproduces the probe: a narrative
    that still carries the model's claims renders without them."""
    _, go = real_run
    agg, _ = await go()
    stored = copy.deepcopy(agg)
    stored["narrative"] = copy.deepcopy(LLM_NARRATIVE)
    narr = resolve_narrative(stored)
    assert narr["accessibility_assessment"].startswith("Analiza dostępności nie dała oceny")
    assert narr["priority_recommendations"][1]["action"].startswith("Powtórzyć pomiar PageSpeed")
    assert resolve_narrative(copy.deepcopy(stored)) == narr, "stable across renders"


def test_an_archived_run_with_no_ledger_renders_as_it_was_delivered():
    stats = {"participant_count": 3, "heuristic": {}, "accessibility": {"avg_score": None},
             "viewport": {"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 3}}
    agg = {"website": "x.pl", "statistics": stats, "narrative": copy.deepcopy(LLM_NARRATIVE)}
    narr = resolve_narrative(agg)
    assert narr["accessibility_assessment"] == LLM_NARRATIVE["accessibility_assessment"]
    assert narr["responsiveness_assessment"] == LLM_NARRATIVE["responsiveness_assessment"]


# --- measurement_caveat is rendered beside the recommendation -------------------

def _caveat_agg():
    narr = guard_unmeasured(_narr(), _stats(), PS_FAILED)
    return {"website": "x.pl", "narrative": narr, "pagespeed": PS_FAILED,
            "statistics": {"participant_count": 2, "heuristic": {}}}


def test_the_html_prints_the_measurement_caveat():
    assert "nie ma dowodu na problem z wydajnością" in generate_html_report(_caveat_agg())


def test_the_word_file_prints_the_measurement_caveat(tmp_path):
    d = Document(build_docx(_caveat_agg(), tmp_path, tmp_path / "r.docx"))
    cells = "\n".join(c.text for t in d.tables for row in t.rows for c in row.cells)
    assert "nie ma dowodu na problem z wydajnością" in cells


# --- a failed narrator call --------------------------------------------------

def test_a_failed_narrator_call_says_so_instead_of_rendering_as_a_normal_report():
    stats = {"participant_count": 2, "heuristic": {"severity_counts": {}, "by_heuristic": []}}
    failed = resolve_narrative({"statistics": stats, "narrative": {"error": "LLM timeout"}})
    assert "analiza narracyjna modelu nie powiodła się" in failed["executive_summary"]
    never = resolve_narrative({"statistics": stats, "narrative": {}})
    assert "nie powiodła się" not in never["executive_summary"], "no call was recorded"


# --- PageSpeed claims: stems, metric names, a timeout only beside a speed word ------

def _after(narr, ps=PS_FAILED, stats=None):
    return guard_unmeasured(narr, stats or _stats(), ps)


@pytest.mark.parametrize("claim", [
    "Szybkie ładowanie strony.",
    "Strona ładuje się wolno na każdym ekranie.",
    "Wolno się ładuje główny obraz.",
    "LCP przekracza 4 sekundy.",
    "Wskaźnik CLS jest zły.",
    "Ładowanie strony jest szybkie.",
    "Czas ładowania strony jest długi.",
    "Wynik w Lighthouse jest niski.",
])
def test_a_performance_claim_in_any_inflection_is_taken_back(claim):
    narr = _after(_narr(key_strengths=["Czytelna nawigacja", claim],
                        executive_summary=f"Badanie ujawniło problem ze sklepami. {claim}"))
    assert narr["key_strengths"] == ["Czytelna nawigacja"]
    assert narr["executive_summary"] == "Badanie ujawniło problem ze sklepami."


@pytest.mark.parametrize("text", [
    "Wydłuż timeout sesji po 15 minutach bezczynności.",
    "Lokalizator sklepów nie ładuje się po wpisaniu miasta.",
    "Formularz kończy się komunikatem o przekroczeniu limitu czasu sesji.",
])
def test_text_about_something_else_is_not_a_performance_claim(text):
    """A timeout of a login session is a setting; a locator that does not load is a
    defect the prompt asks to report like any other."""
    rec = {"priority": 1, "area": "Logowanie", "action": text, "expected_impact": "Mniej wylogowań."}
    narr = _after(_narr(key_strengths=[text], priority_recommendations=[dict(rec)]))
    assert narr["priority_recommendations"] == [rec]
    assert narr["key_strengths"] == [text]


def test_two_performance_recommendations_are_one_row_and_the_rest_renumbered():
    recs = [{"priority": 1, "area": "Wydajność", "action": "Poprawić PageSpeed.", "expected_impact": "x"},
            {"priority": 2, "area": "Lokalizator", "action": "Naprawić wyszukiwarkę.", "expected_impact": "y"},
            {"priority": 3, "area": "Czas ładowania", "action": "Skrócić czas ładowania strony.",
             "expected_impact": "z"}]
    narr = _after(_narr(priority_recommendations=copy.deepcopy(recs)))
    rows = narr["priority_recommendations"]
    assert [r["area"] for r in rows] == ["Pomiar PageSpeed", "Lokalizator"]
    assert [r["priority"] for r in rows] == [1, 2]
    assert _after(copy.deepcopy(narr))["priority_recommendations"] == rows, "stable on its own output"


def test_a_summary_keeps_its_paragraphs_when_a_sentence_is_taken_out():
    summary = "Nawigacja jest czytelna.\n\nStrona ma problemy z wydajnością. Koszyk działa."
    narr = _after(_narr(executive_summary=summary))
    assert narr["executive_summary"] == "Nawigacja jest czytelna.\n\nKoszyk działa."


PS_MOBILE_FAILED = {"url": "x", "mobile": {"error": "timed out"}, "desktop": {"performance_score": 38}}
PS_DESKTOP_FAILED = {"url": "x", "mobile": {"performance_score": 38}, "desktop": {"error": "timed out"}}


def test_one_failed_strategy_takes_back_only_the_claims_about_that_device():
    recs = [{"priority": 1, "area": "Wydajność mobilna", "action": "Poprawić wynik PageSpeed na telefonie.",
             "expected_impact": "x"},
            {"priority": 2, "area": "Wydajność serwisu", "action": "Skrócić czas ładowania strony.",
             "expected_impact": "y"}]
    narr = _after(_narr(priority_recommendations=copy.deepcopy(recs),
                        executive_summary="Wydajność mobilna jest słaba. Wydajność ogółem budzi zastrzeżenia."),
                  PS_MOBILE_FAILED)
    first, second = narr["priority_recommendations"]
    assert first["area"] == "Pomiar PageSpeed (telefon)"
    assert first["action"].startswith("Powtórzyć pomiar PageSpeed na telefonie") and "nie wada strony" in first["action"]
    assert "na telefonie" in first["measurement_caveat"]
    assert second == recs[1], "the desktop score still stands behind it"
    assert narr["executive_summary"] == "Wydajność ogółem budzi zastrzeżenia."
    assert "PageSpeed na telefonie nie powiódł się" in narr["methodology_note"]
    assert _after(copy.deepcopy(narr), PS_MOBILE_FAILED) == narr


def test_a_failed_desktop_run_takes_back_the_claims_about_the_desktop():
    recs = [{"priority": 1, "area": "Wydajność", "action": "Poprawić PageSpeed na komputerze.",
             "expected_impact": "x"},
            {"priority": 2, "area": "Wydajność", "action": "Poprawić PageSpeed na telefonie.",
             "expected_impact": "y"}]
    narr = _after(_narr(priority_recommendations=copy.deepcopy(recs)), PS_DESKTOP_FAILED)
    first, second = narr["priority_recommendations"]
    assert first["action"].startswith("Powtórzyć pomiar PageSpeed na komputerze")
    assert second == recs[1]


def test_a_mobile_performance_deduction_means_the_mobile_run_was_measured():
    stats = _stats(score_breakdown={"deductions": [{"kind": "performance", "amount": 0.5}]})
    before = _narr(priority_recommendations=[{"priority": 1, "area": "Wydajność mobilna",
                                               "action": "Poprawić PageSpeed na telefonie.",
                                               "expected_impact": "x"}])
    narr = _after(copy.deepcopy(before), PS_FAILED, stats)
    assert narr["priority_recommendations"][0]["action"] == "Poprawić PageSpeed na telefonie.", \
        "the score was computed from a measurement, whatever the file says now"


# --- the other failed passes: summary, strengths and recommendations ---------------

def test_a_failed_accessibility_pass_loses_its_claims_beside_the_assessment():
    narr = _after(_narr(
        executive_summary="Nawigacja jest czytelna. Strona jest dostępna dla osób z niepełnosprawnościami. "
                          "Produkt jest dostępny w sklepie, a dostępność rozmiarów jest dobra.",
        key_strengths=["Wysoka dostępność cyfrowa serwisu", "Czytelna nawigacja", "Wysoka dostępność produktów"],
        priority_recommendations=[
            {"priority": 1, "area": "Dostępność", "action": "Dodać teksty alternatywne do obrazów (WCAG 1.1.1).",
             "expected_impact": "Lepsza dostępność."},
            {"priority": 2, "area": "Lokalizator", "action": "Naprawić wyszukiwarkę sklepów.",
             "expected_impact": "Ukończenie zadania."}]), PS_OK)
    assert narr["executive_summary"] == ("Nawigacja jest czytelna. "
                                         "Produkt jest dostępny w sklepie, a dostępność rozmiarów jest dobra.")
    assert narr["key_strengths"] == ["Czytelna nawigacja", "Wysoka dostępność produktów"], \
        "availability of goods is not accessibility"
    first, second = narr["priority_recommendations"]
    assert first["area"] == "Analiza dostępności" and first["action"].startswith("Powtórzyć analizę dostępności")
    assert "nie ma dowodu na problem z dostępnością" in first["measurement_caveat"]
    assert second["action"] == "Naprawić wyszukiwarkę sklepów."
    assert narr["methodology_note"].count("Analiza dostępności nie powiodła się") == 1
    assert _after(copy.deepcopy(narr), PS_OK) == narr


@pytest.mark.parametrize("area, action", [
    ("Dostępność serwisu", "Zmienić konfigurację Cloudflare, która blokuje prawdziwych użytkowników."),
    ("Użyteczność mobilna", "Naprawić błędy kontrastu na wersji mobilnej zgodnie z WCAG AA."),
    ("Strona główna i Pierwsze wrażenie", "Zmienić hierarchię wizualną strony głównej."),
])
def test_a_recommendation_another_pass_stands_behind_is_not_taken_back(area, action):
    """"Dostępność serwisu" is also the site being up, WCAG is cited by the contrast and
    tap-target scans, and a section named "pierwsze wrażenie" can rest on a heuristic
    finding: none of them is a claim of the failed accessibility or first-impression pass."""
    stats = _stats(audit_failures=[{"persona": f"P{i}", "passes": ["x"],
                                    "pass_ids": ["accessibility", "first_impression"]} for i in range(3)],
                   first_impression={"avg_score": None, "scored": 0, "of": 3})
    rec = {"priority": 1, "area": area, "action": action, "expected_impact": "Ukończenie zadania."}
    narr = _after(_narr(priority_recommendations=[dict(rec)]), PS_OK, stats)
    assert narr["priority_recommendations"] == [rec]


def test_a_partly_failed_accessibility_pass_keeps_its_claims():
    stats = _stats(audit_failures=[{"persona": "P0", "passes": ["x"], "pass_ids": ["accessibility"]}],
                   accessibility={"avg_score": 3, "scored": 2, "of": 3})
    before = _narr(key_strengths=["Wysoka dostępność cyfrowa serwisu"],
                   executive_summary="Strona jest dostępna dla osób z niepełnosprawnościami.")
    narr = _after(copy.deepcopy(before), PS_OK, stats)
    assert narr["key_strengths"] == before["key_strengths"]
    assert narr["executive_summary"] == before["executive_summary"]


def test_a_failed_first_impression_pass_loses_its_claims_beside_the_assessment():
    stats = _stats(audit_failures=[{"persona": f"P{i}", "passes": ["x"], "pass_ids": ["first_impression"]}
                                   for i in range(3)],
                   first_impression={"avg_score": None, "scored": 0, "of": 3})
    narr = _after(_narr(key_strengths=["Mocne pierwsze wrażenie", "Czytelna nawigacja"],
                        executive_summary="Pierwsze wrażenie jest dobre. Sklepy są trudne do znalezienia."),
                  PS_OK, stats)
    assert narr["key_strengths"] == ["Czytelna nawigacja"]
    assert narr["executive_summary"] == "Sklepy są trudne do znalezienia."
    assert narr["first_impression_assessment"].startswith("Test pierwszego wrażenia nie dał oceny")


def test_a_scan_nobody_has_loses_its_responsiveness_claims_but_not_a_session_finding():
    stats = _stats(viewport={"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0, "devices": []})
    narr = _after(_narr(
        key_strengths=["Strona działa poprawnie na telefonach", "Czytelna nawigacja"],
        executive_summary="Strona jest w pełni responsywna. Na telefonie baner zasłania cały ekran.",
        priority_recommendations=[{"priority": 1, "area": "Responsywność", "action": "Poprawić tablet.",
                                   "expected_impact": "x"}]), PS_OK, stats)
    assert narr["key_strengths"] == ["Czytelna nawigacja"]
    assert narr["executive_summary"] == "Na telefonie baner zasłania cały ekran.", \
        "what a persona saw on a phone is a finding, not a scan result"
    assert narr["priority_recommendations"][0]["action"].startswith("Powtórzyć skan responsywności")
    assert "Skan responsywności nie dał wyników" in narr["methodology_note"]
    assert _after(copy.deepcopy(narr), PS_OK, stats) == narr


def test_a_summary_with_nothing_left_says_what_was_not_measured():
    narr = _after(_narr(executive_summary="Strona jest dostępna dla osób z niepełnosprawnościami."), PS_OK)
    assert narr["executive_summary"] == "Nie oceniono dostępności strony."


# --- the survey count reaches the narrator even when no artifact was written ---------

def test_a_survey_that_raised_and_wrote_nothing_still_reaches_the_prompt_block():
    stats = _stats(audit_failures=[{"persona": "P1", "persona_dir": "p1", "passes": ["ankieta"],
                                    "pass_ids": ["survey"]}], survey={"failed_surveys": []})
    status = measurement_status(stats, PS_OK)
    assert status["survey"] == {"failed": 1, "of": 3}
    assert "ankieta: błąd narzędzia dla 1 z 3 uczestników" in measurement_status_text(status)


def test_the_larger_of_the_artifact_and_ledger_survey_counts_is_used():
    stats = _stats(survey={"failed_surveys": [{"persona_dir": "p1"}, {"persona_dir": "p2"}]},
                   audit_failures=[{"persona": "P1", "passes": ["ankieta"], "pass_ids": ["survey"]}])
    assert measurement_status(stats, PS_OK)["survey"]["failed"] == 2


# --- the first impression names its weak device ---------------------------------------

FI_SPLIT = {"avg_score": 2.27, "scored": 6, "of": 6, "device_gap": 1.6, "device_dependent": True,
            "weakest_device": "mobile",
            "by_device": {"desktop": {"avg_score": 3.07, "n": 3}, "mobile": {"avg_score": 1.47, "n": 3}}}


def _fi_stats(**fi):
    return _stats(audit_failures=[], survey={}, first_impression={**FI_SPLIT, **fi})


def test_a_blended_first_impression_gets_the_per_device_line_appended():
    narr = guard_unmeasured(_narr(first_impression_assessment="Pierwsze wrażenie 2,27/5 — negatywne."),
                            _fi_stats(), PS_OK)
    text = narr["first_impression_assessment"]
    assert text.startswith("Pierwsze wrażenie 2,27/5 — negatywne.")
    assert "Pierwsze wrażenie według urządzenia: komputer 3,07 (n=3) · telefon 1,47 (n=3)" in text
    assert "wynik zależy od urządzenia" in text
    assert guard_unmeasured(copy.deepcopy(narr), _fi_stats(), PS_OK) == narr, "appended once"


@pytest.mark.parametrize("text", [
    "Na telefonie pierwsze wrażenie jest słabe.",
    "Komputer 3,07, ale tylko 1,47 na ekranie mobilnym.",
    "Ocena na urządzeniach mobilnych wynosi 1.47.",
])
def test_an_assessment_that_names_the_weak_device_or_its_average_is_left_alone(text):
    narr = guard_unmeasured(_narr(first_impression_assessment=text), _fi_stats(), PS_OK)
    assert narr["first_impression_assessment"] == text


def test_a_gap_under_a_point_or_no_split_adds_no_device_line():
    close = _fi_stats(device_dependent=False, device_gap=0.4)
    close.pop("weakest_device", None)
    assert guard_unmeasured(_narr(), close, PS_OK)["first_impression_assessment"] == "Pierwsze wrażenie jest dobre."
    one_device = _stats(audit_failures=[], survey={}, first_impression={"avg_score": 3.4, "scored": 3, "of": 3})
    assert guard_unmeasured(_narr(), one_device, PS_OK)["first_impression_assessment"] == "Pierwsze wrażenie jest dobre."
    assert guard_unmeasured(_narr(first_impression_assessment=""), _fi_stats(), PS_OK)[
        "first_impression_assessment"] == "", "no assessment to qualify"
