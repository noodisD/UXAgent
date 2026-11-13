"""The aggregate narrator is held to the landing measurements as the first-impression pass is.

In the regenerated report of the bnpparibas.pl run the executive summary still called the
mandated UOKiK notice a "masywny blok, który przytłacza interfejs i utrudnia dotarcie do
właściwej nawigacji oraz wezwań do działania", while the measurements say: an in-flow block,
14-15% of the desktop first screen and 32-34% of the mobile one, covering neither navigation
nor buttons, closable. The prompt now carries those facts and a code check puts them in place
of the sentence.
"""
import copy
import re
from pathlib import Path

from simulated_web_agent.main.landing_facts import (
    MEASURED_NOTE, NO_NOTICE_MEASUREMENTS, guard_measured_notice_claims, landing_notice_rollup,
    measured_notice_sentence, summarize_notice_rollup,
)
from simulated_web_agent.main.reporter import resolve_narrative
from simulated_web_agent.main.trace_store import TraceStore

SRC = Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
PROMPT = (SRC / "audit_prompts" / "aggregate_synthesize.txt").read_text(encoding="utf-8")

REAL_SENTENCE = ("Główną przeszkodą rzutującą na odbiór strony na samym jej początku jest masywny blok z "
                 "obowiązkowym komunikatem prawnym UOKiK, który przytłacza interfejs i utrudnia dotarcie do "
                 "właściwej nawigacji oraz wezwań do działania.")
UOKIK = "Komunikat o decyzji Prezesa Urzędu Ochrony Konkurencji i Konsumentów. Na stronie UOKiK możesz zobaczyć"


def _notice(**over):
    base = {"text": UOKIK, "mandated": True, "kind": "in_flow", "is_overlay": False, "covers_content": False,
            "covers_navigation": False, "covers_ctas": False, "has_close_control": True, "viewport_share": 0.143}
    base.update(over)
    return base


def _store(tmp_path, name, notice, width=1440, height=900):
    store = TraceStore(tmp_path / name)
    store.write_landing_facts({"viewport": {"width": width, "height": height}, "notices": [notice]})
    return store


def _rollup(tmp_path, desktop=None, mobile=None):
    stores = []
    for i, share in enumerate((0.143, 0.146)):
        stores.append(_store(tmp_path, f"d{i}", {**_notice(), "viewport_share": share, **(desktop or {})}))
    for i, share in enumerate((0.317, 0.343)):
        stores.append(_store(tmp_path, f"m{i}", {**_notice(), "viewport_share": share, **(mobile or {})},
                             width=390, height=844))
    return landing_notice_rollup(stores)


def _narr(**over):
    base = {"executive_summary": f"Badanie 4 uczestników. {REAL_SENTENCE} Kalkulator jest trudno dostępny.",
            "first_impression_assessment": "Pierwsze wrażenie jest przeciętne.", "methodology_note": "Badanie.",
            "critical_issues": [], "serious_issues": [], "minor_issues": []}
    base.update(over)
    return base


def test_the_real_summary_sentence_is_rewritten_to_the_measurements(tmp_path):
    roll = _rollup(tmp_path)
    narr = guard_measured_notice_claims(_narr(), roll)
    summary = narr["executive_summary"]
    assert "przytłacza" not in summary and "utrudnia dotarcie" not in summary and "masywny" not in summary
    assert "blok w przepływie strony" in summary
    assert "na komputerze 14-15%" in summary and "na telefonie 32-34% pierwszego ekranu" in summary
    assert "nie zasłania nawigacji ani przycisków" in summary
    assert summary.startswith("Badanie 4 uczestników.") and summary.endswith("Kalkulator jest trudno dostępny.")
    assert narr["methodology_note"].endswith(MEASURED_NOTE)


def test_issue_texts_and_the_assessment_get_the_same_correction(tmp_path):
    roll = _rollup(tmp_path)
    narr = guard_measured_notice_claims(_narr(
        first_impression_assessment="Okno z komunikatem UOKiK zasłania menu. Ocena 2,8.",
        minor_issues=[{"impact": "Komunikat prawny blokuje przyciski.", "recommendation": "Dodać link."}]), roll)
    assert "zasłania menu" not in narr["first_impression_assessment"]
    assert "Ocena 2,8." in narr["first_impression_assessment"]
    assert "blokuje" not in narr["minor_issues"][0]["impact"]
    assert narr["minor_issues"][0]["recommendation"] == "Dodać link."


def test_an_overlay_that_covers_is_left_alone(tmp_path):
    covering = {"kind": "overlay", "is_overlay": True, "covers_navigation": True, "covers_ctas": True,
                "covers_content": True}
    narr = _narr()
    for roll in (_rollup(tmp_path, mobile=covering), _rollup(tmp_path / "x", desktop=covering, mobile=covering)):
        assert measured_notice_sentence(roll) is None
        assert guard_measured_notice_claims(copy.deepcopy(narr), roll) == narr


def test_an_overlay_is_not_an_in_flow_block_even_when_it_covers_nothing_measured(tmp_path):
    roll = _rollup(tmp_path, desktop={"kind": "overlay", "is_overlay": True})
    assert guard_measured_notice_claims(_narr(), roll) == _narr()


def test_without_facts_nothing_is_touched(tmp_path):
    for landing in (None, {}, {"left_out": 2}, {"measured_sessions": 2, "notices": [], "only_mandated": False}):
        assert guard_measured_notice_claims(_narr(), landing) == _narr()


def test_a_legacy_rollup_without_devices_is_unchanged(tmp_path):
    legacy = {"measured_sessions": 6, "notices": [{"title": "UOKiK", "mandated": True, "kind": "in_flow",
                                                   "sessions": 6}], "mandated": True, "only_mandated": True}
    assert guard_measured_notice_claims(_narr(), legacy) == _narr()
    assert summarize_notice_rollup(legacy) == NO_NOTICE_MEASUREMENTS


def test_sentences_about_other_things_are_untouched(tmp_path):
    roll = _rollup(tmp_path)
    text = ("Widżet czatu zasłania przycisk „Oblicz ratę”. Baner aplikacji GOmobile blokuje górę ekranu. "
            "Okno cookies przytłacza stronę. Nakładka z promocją ukrywa ofertę. "
            "Komunikat UOKiK nie zasłania menu.")
    narr = guard_measured_notice_claims(_narr(executive_summary=text), roll)
    assert narr["executive_summary"] == text
    assert narr["methodology_note"] == "Badanie."


def test_the_check_is_idempotent_and_the_note_is_added_once(tmp_path):
    roll = _rollup(tmp_path)
    once = guard_measured_notice_claims(_narr(), roll)
    twice = guard_measured_notice_claims(copy.deepcopy(once), roll)
    assert twice == once
    assert once["methodology_note"].count(MEASURED_NOTE) == 1
    again = guard_measured_notice_claims(_narr(executive_summary=REAL_SENTENCE + " " + REAL_SENTENCE.replace(
        "masywny", "ogromny")), roll)
    assert again["executive_summary"].count("blok w przepływie strony") == 1


def test_a_stored_aggregate_is_corrected_when_it_is_rendered_again(tmp_path):
    agg = {"statistics": {"landing_notices": _rollup(tmp_path)}, "narrative": _narr(priority_recommendations=[])}
    rendered = resolve_narrative(copy.deepcopy(agg))
    assert "przytłacza" not in rendered["executive_summary"]
    assert rendered["methodology_note"].count(MEASURED_NOTE) == 1
    assert resolve_narrative(copy.deepcopy(agg)) == rendered


def test_the_prompt_block_states_the_facts_per_device(tmp_path):
    block = summarize_notice_rollup(_rollup(tmp_path))
    assert "komputer 1440×900 px, 2 sesji" in block and "telefon 390×844 px, 2 sesji" in block
    assert "zajmuje 14-15% powierzchni pierwszego ekranu" in block
    assert "zajmuje 32-34% powierzchni pierwszego ekranu" in block
    assert "nie zasłania nawigacji ani przycisków" in block and "można go zamknąć" in block
    assert "OBOWIĄZKOWY" in block


def test_a_covering_notice_is_stated_as_covering_in_the_prompt_block(tmp_path):
    block = summarize_notice_rollup(_rollup(tmp_path, mobile={"covers_navigation": True}))
    assert "ZASŁANIA nawigację lub przyciski" in block


def test_no_measurements_means_no_geometry_claims_in_the_prompt():
    assert summarize_notice_rollup({}) == NO_NOTICE_MEASUREMENTS
    assert "nie wypowiadaj się o rozmiarze" in NO_NOTICE_MEASUREMENTS


def test_the_prompt_formats_with_the_block_and_has_the_rules(tmp_path):
    # The same str.format the reporter performs: braces in the block or the rules would raise or leak.
    wanted = set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", PROMPT))
    assert "landing_measurements" in wanted
    block = summarize_notice_rollup(_rollup(tmp_path)) + " {literal}"
    filled = PROMPT.format(**{k: "x" for k in wanted if k != "landing_measurements"}, landing_measurements=block)
    assert "{literal}" in filled and "nie zasłania nawigacji ani przycisków" in filled
    flat = " ".join(filled.split())
    assert "blok w przepływie strony nie jest oknem" in flat
    assert "Gdy pomiaru brak, nie wypowiadaj się o geometrii" in flat


def test_the_rollup_keeps_its_old_shape_beside_the_devices(tmp_path):
    roll = _rollup(tmp_path)
    assert roll["measured_sessions"] == 4 and roll["only_mandated"] is True
    assert roll["notices"][0]["sessions"] == 4
    assert [d["device"] for d in roll["devices"]] == ["desktop", "mobile"]


def test_a_notice_titled_with_a_claim_word_does_not_make_its_own_wording_a_claim(tmp_path):
    stores = [_store(tmp_path, "a", _notice(text="Okno informacyjne: komunikat o pożyczce", mandated=False))]
    roll = landing_notice_rollup(stores)
    narr = _narr(executive_summary="Komunikat o pożyczce przytłacza ekran.")
    once = guard_measured_notice_claims(narr, roll)
    assert "Okno informacyjne" in once["executive_summary"]
    assert guard_measured_notice_claims(copy.deepcopy(once), roll) == once
