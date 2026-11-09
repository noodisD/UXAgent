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


def test_no_pagespeed_result_at_all_is_not_called_a_failure():
    before = _narr()
    narr = guard_unmeasured(copy.deepcopy(before), _stats(), None)
    assert narr["priority_recommendations"] == before["priority_recommendations"]


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
    narr = guard_unmeasured(copy.deepcopy(before), legacy, None)
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
