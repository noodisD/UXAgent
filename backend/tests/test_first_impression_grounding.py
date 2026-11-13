"""The 5-second test describes the screen from measurements, and never asks to hide a notice the site must show.

The first real audit of bnpparibas.pl (6 personas): every first impression called the UOKiK
notice - an in-flow block, white, 2 px frame, 18 px text (12 px on mobile) - a pop-up that
"covers the screen" in "small grey print", a chat circle clipping 10% of one button's edge
became an "almost completely covered" button, and "restyle the notice to be more discreet and
less alarming" became priority #1 of the aggregate. The page is now measured when the persona
lands (main/landing_facts.py), the pass is told what the numbers are and that a notice marked
mandated may only get legibility advice, and the same rule is applied to the output in code.
"""
import copy
import inspect
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.agent.prompt_registry import registry as _prompts
from simulated_web_agent.main.auditor import _run_first_impression
from simulated_web_agent.main.landing_facts import NO_FACTS, landing_notice_rollup, summarize_landing_facts
from simulated_web_agent.main.mandated_notice import LEFT_OUT_NOTE, LEGIBILITY_ONLY
from simulated_web_agent.main.reporter import generate_aggregated_report, resolve_narrative
from simulated_web_agent.main.trace_store import TraceStore
from tests.conftest import make_agent_report, make_finding

UOKIK = ("Komunikat o decyzji Prezesa Urzędu Ochrony Konkurencji i Konsumentów Na stronie UOKiK możesz "
         "zobaczyć decyzję zobowiązującą Prezesa UOKiK nr RKR-3/2026.")

# The eight real suggested changes about the notice, verbatim (run 2026-11-10_16-15-42_cfe5).
REAL_SUGGESTIONS = [
    "Zmienić stylizację obowiązkowego komunikatu UOKiK (usunąć zielonkawe tło i grubą ramkę, zostawiając sam "
    "tekst na białym tle), aby wyglądał jak neutralna nota prawna, a nie systemowy alarm bezpieczeństwa banku.",
    "Zwinąć komunikat UOKiK do formy wąskiego paska z tekstem 'Komunikat...' i przyciskiem 'Rozwiń', by "
    "odsłonić główną ofertę na ekranie startowym.",
    "Usunąć zielone obramowanie okna UOKiK i zmniejszyć w nim marginesy, aby wizualnie oddzielić go od "
    "elementów nawigacyjnych banku.",
    "Zwinąć komunikat UOKiK do formy wąskiego, dyskretnego paska na dole ekranu, aby odsłonić główny interfejs banku.",
    "Zmniejszyć obszar zajmowany przez 'Komunikat o decyzji Prezesa Urzędu Ochrony Konkurencji i Konsumentów', "
    "zwijając ten długi tekst do jednego, rozwijanego wiersza.",
    "Zmniejszyć inwazyjność okna 'Komunikat o decyzji Prezesa UOKiK', formatując je jako płaski pasek tekstowy "
    "pod głównym menu, a nie wielki popup zasłaniający ekran.",
    "Przeredagować (w ramach możliwości prawnych) lub wizualnie stonować nagłówek komunikatu UOKiK, aby nie "
    "wyglądał jak ostrzeżenie o krytycznym błędzie bezpieczeństwa na powitaniu.",
    "Zminimalizować wizualnie ramkę 'Komunikat o decyzji...', ponieważ wyeksponowane słowa o 'transakcjach "
    "nieautoryzowanych' odstraszają na samym wejściu.",
]
REAL_RECOMMENDATION = ("Zmienić stylizację komunikatu o decyzji UOKiK na bardziej dyskretną i mniej alarmującą "
                       "(np. płaski pasek do rozwinięcia zamiast okna z ramką na górze ekranu).")


def _facts(mandated=True, kind="in_flow", widgets=None, text=UOKIK):
    return {"viewport": {"width": 390, "height": 844, "dpr": 1}, "notices": [{
        "text": text, "kind": kind, "position": "relative", "viewport_share": 0.317,
        "viewport_height_share": 0.343, "font_size_px": 12, "color": "#373a3c", "background_color": "#ffffff",
        "border_px": 2, "border_color": "#00834f", "covers_content": False, "covers_navigation": False,
        "covers_ctas": False, "covered": [], "has_close_control": True, "close_label": "Zamknij",
        "mandated": mandated, "mandated_reason": "decyzja organu" if mandated else ""}],
        "floating_widgets": widgets or [], "nav_labels": ["Klienci indywidualni", "Kontakt"],
        "ctas": [{"text": "OTWÓRZ KONTO"}], "top_controls": ["zaloguj", "menu"]}


def _reply(**changes):
    return json.dumps({
        "overall_score": 2.4, "clarity_summary": "Podsumowanie.",
        "scores": {"purpose_clarity": 3, "navigation_visibility": 2, "call_to_action": 2,
                   "visual_hierarchy": 3, "trustworthiness": 3},
        "suggested_changes": {"purpose_clarity": "", "navigation_visibility": "", "call_to_action": "",
                              "visual_hierarchy": "", "trustworthiness": "", **changes},
        "persona_lens": "Zwróciłby uwagę na menu.", "strengths": ["a"], "weaknesses": ["b"]})


def _store(tmp_path, facts=None):
    store = TraceStore(tmp_path / "persona")
    shot = store.screenshot_path(0)
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    store.write_basic_info({"site_context": "bank"})
    store.write_persona_card({"name": "Ola", "description": "Persona: Ola\nAge: 52"})
    if facts is not None:
        store.write_landing_facts(facts)
    return store


def _sent(mock) -> str:
    return " ".join(p.get("text", "") for p in mock.await_args.args[0][0]["content"] if isinstance(p, dict))


async def _run(store, reply):
    mock = AsyncMock(return_value=reply)
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_first_impression(store)
    return result, mock


# --- the prompt -------------------------------------------------------------------------

def test_the_prompt_formats_and_carries_the_facts_block():
    prompt = _prompts.get("first_impression").format(
        persona="Ola", site_context="Bank", landing_facts="FAKTY-TU")
    assert "ZMIERZONE FAKTY O STRONIE STARTOWEJ" in prompt and "FAKTY-TU" in prompt
    assert "{landing_facts}" not in prompt and "{persona}" not in prompt
    assert '"suggested_changes"' in prompt, "the JSON skeleton survives str.format"


def test_the_prompt_forbids_what_the_real_run_did():
    prompt = _prompts.get("first_impression").format(persona="Ola", site_context="Bank", landing_facts="x")
    for rule in (
            "Nie nazywaj bloku okienkiem, pop-upem, nakładką ani oknem modalnym, jeśli pomiar nie wykazał",
            "Nie pisz, że element zasłania, ukrywa albo blokuje",
            "podawać zmierzony procent; kilka procent to drobna usterka",
            "nie charakteryzuj geometrii, koloru ani rozmiaru",
            "Komunikat oznaczony jako OBOWIĄZKOWY",
            "NIE zalecaj, by go ukryć, zmniejszyć",
            "Wolno zalecać wyłącznie czytelność: kontrast, rozmiar tekstu, długość linii",
            "nie opisuj, co osoba zrobiła, zauważyła ani",
            "The element a suggested change names must exist in the measured facts",
    ):
        assert rule in prompt, rule
    assert "a hypothesis about what this person would plausibly pay attention to" in prompt
    assert "what this person noticed that a different person would not" not in prompt


# --- what the pass is given -------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_measured_facts_reach_the_prompt(tmp_path):
    store = _store(tmp_path, _facts())
    _, mock = await _run(store, _reply())
    text = _sent(mock)
    assert "ZMIERZONE FAKTY" in text and "blok w przepływie strony (NIE jest oknem, pop-upem ani nakładką" in text
    assert "34% jego wysokości" in text and "tekst 12 px" in text and "ramka 2 px #00834f" in text
    assert "OBOWIĄZKOWY" in text and "OTWÓRZ KONTO" in text
    assert NO_FACTS not in text


@pytest.mark.asyncio
async def test_a_floating_widget_reaches_the_prompt_with_its_measured_overlap(tmp_path):
    widgets = [{"tag": "div", "id": "", "class": "chat", "label": "Czat", "shape": "round",
                "rect": {"w": 92, "h": 92}, "viewport_share": 0.026,
                "overlaps": [{"cta": "OTWÓRZ KONTO", "overlap_share": 0.1, "label_overlapped": False,
                              "label_overlap_share": 0}]}]
    _, mock = await _run(_store(tmp_path, _facts(widgets=widgets)), _reply())
    text = _sent(mock)
    assert "nachodzi na przycisk „OTWÓRZ KONTO” w 10% jego powierzchni" in text
    assert "napis przycisku jest w całości widoczny" in text


@pytest.mark.asyncio
async def test_without_the_file_the_pass_is_told_to_make_no_geometry_claim(tmp_path):
    store = _store(tmp_path)
    assert store.read_landing_facts() is None
    result, mock = await _run(store, _reply())
    text = _sent(mock)
    assert NO_FACTS in text and "nie opisuj geometrii, kolorów ani rozmiaru czcionki" in text
    assert result["scores"]["purpose_clarity"] == 3, "an older run is scored as before"
    assert "mandated_notice_left_out" not in result


def test_a_notice_that_is_not_mandated_is_not_shielded_in_the_block():
    block = summarize_landing_facts(_facts(mandated=False, kind="overlay", text="Otwórz konto w aplikacji GOmobile"))
    assert "OBOWIĄZKOWY" not in block and "nakładka na treść strony" in block


# --- the guard on the pass's own output -------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("sentence", REAL_SUGGESTIONS)
async def test_each_real_suggestion_to_dilute_the_notice_is_taken_back(tmp_path, sentence):
    result, _ = await _run(_store(tmp_path, _facts()), _reply(visual_hierarchy=sentence))
    assert result["suggested_changes"]["visual_hierarchy"] == LEGIBILITY_ONLY
    assert result["mandated_notice_left_out"] is True


@pytest.mark.asyncio
async def test_the_guard_works_without_measurements_when_the_sentence_names_the_regulator(tmp_path):
    result, _ = await _run(_store(tmp_path), _reply(purpose_clarity=REAL_SUGGESTIONS[1]))
    assert result["suggested_changes"]["purpose_clarity"] == LEGIBILITY_ONLY


@pytest.mark.asyncio
async def test_an_app_banner_restyle_is_kept(tmp_path):
    banner = "Zmniejszyć baner aplikacji GOmobile na górze ekranu, aby odsłonić menu."
    result, _ = await _run(_store(tmp_path, _facts()), _reply(call_to_action=banner))
    assert result["suggested_changes"]["call_to_action"] == banner
    assert "mandated_notice_left_out" not in result


@pytest.mark.asyncio
async def test_a_weakness_asking_to_shrink_the_notice_is_dropped(tmp_path):
    reply = json.loads(_reply())
    reply["weaknesses"] = ["Brak numeru infolinii.", "Komunikat UOKiK należy zmniejszyć lub ukryć."]
    result, _ = await _run(_store(tmp_path, _facts()), json.dumps(reply))
    assert result["weaknesses"] == ["Brak numeru infolinii."]


@pytest.mark.asyncio
async def test_a_failed_pass_stays_an_error_and_is_not_stamped(tmp_path):
    result, _ = await _run(_store(tmp_path, _facts()), '["tylko", "lista"]')
    assert result == {"error": "unexpected_shape"}


def test_the_landing_capture_runs_after_the_screenshot_and_before_the_first_observation():
    from simulated_web_agent.main import experiment

    src = inspect.getsource(experiment)
    shot = src.index("landing_screenshot_path()")
    capture = src.index("capture_landing_facts(env.page, store)")
    assert shot < capture < src.index("obs = await env.observation()")


# --- the trace store ----------------------------------------------------------------------

def test_landing_facts_round_trip_beside_the_screenshot(tmp_path):
    store = TraceStore(tmp_path / "p")
    store.write_landing_facts(_facts())
    assert (store.trace_dir / "landing_facts.json").exists()
    assert store.read_landing_facts()["notices"][0]["text"] == UOKIK


def test_an_unreadable_or_foreign_landing_file_reads_as_absent(tmp_path):
    store = TraceStore(tmp_path / "p")
    (store.trace_dir / "landing_facts.json").write_text("{not json", encoding="utf-8")
    assert store.read_landing_facts() is None
    (store.trace_dir / "landing_facts.json").write_text("[1, 2]", encoding="utf-8")
    assert store.read_landing_facts() is None


# --- the aggregate ------------------------------------------------------------------------

def _run_dir(tmp_path, with_facts=True, left_out=False):
    stores = []
    for i in range(2):
        s = TraceStore(tmp_path / f"p{i}")
        report = make_agent_report(findings=[make_finding(heuristic_id=1, severity="poważny")])
        report["trace_dir"] = str(s.trace_dir)
        s.write_ux_report(report)
        s.write_first_impression({**report["first_impression"], **({"mandated_notice_left_out": True} if left_out else {})})
        if with_facts:
            s.write_landing_facts(_facts())
        stores.append(s)
    return stores


def _narrative(**over):
    base = {
        "executive_summary": "Badanie 2 uczestników. Ogromny komunikat UOKiK przytłacza ekran.",
        "overall_ux_score": 3, "key_strengths": ["Czytelna nawigacja"], "critical_issues": [],
        "serious_issues": [], "minor_issues": [],
        "accessibility_assessment": "Brak istotnych barier.", "responsiveness_assessment": "Działa.",
        "first_impression_assessment": "Pierwsze wrażenie jest słabe.",
        "priority_recommendations": [
            {"priority": 1, "area": "Pierwsze wrażenie / UI", "action": REAL_RECOMMENDATION,
             "expected_impact": "Mniejszy niepokój."},
            {"priority": 2, "area": "Nawigacja", "action": "Dodać link do kalkulatora.",
             "expected_impact": "Krótsza ścieżka."}],
        "methodology_note": "Badanie jakościowe."}
    base.update(over)
    return base


async def _aggregate(stores, narrative):
    chat = AsyncMock(return_value=json.dumps(narrative))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        return await generate_aggregated_report(stores, "Sprawdź ofertę", "https://x.pl/")


@pytest.mark.asyncio
async def test_priority_one_about_the_notice_is_replaced_in_the_stored_aggregate(tmp_path):
    agg = await _aggregate(_run_dir(tmp_path), _narrative())
    stats, narr = agg["statistics"], agg["narrative"]
    assert stats["landing_notices"]["only_mandated"] is True and stats["landing_notices"]["measured_sessions"] == 2
    first, second = narr["priority_recommendations"]
    assert first["action"] == LEGIBILITY_ONLY and first["priority"] == 1
    assert second["action"] == "Dodać link do kalkulatora." and second["priority"] == 2
    assert "dyskretn" not in json.dumps(narr, ensure_ascii=False)
    assert narr["methodology_note"].count(LEFT_OUT_NOTE) == 1


@pytest.mark.asyncio
async def test_a_notice_beside_an_app_banner_is_still_named_by_the_regulator_words(tmp_path):
    stores = _run_dir(tmp_path)
    stores[0].write_landing_facts({**_facts(), "notices": _facts()["notices"] + _facts(
        mandated=False, kind="fixed_bar", text="Otwórz konto w aplikacji GOmobile")["notices"]})
    agg = await _aggregate(stores, _narrative())
    assert agg["statistics"]["landing_notices"]["only_mandated"] is False
    assert agg["narrative"]["priority_recommendations"][0]["action"] == LEGIBILITY_ONLY, "it names UOKiK"


@pytest.mark.asyncio
async def test_a_run_without_landing_facts_has_no_notice_statistics_but_the_text_is_still_guarded(tmp_path):
    agg = await _aggregate(_run_dir(tmp_path, with_facts=False), _narrative())
    assert "landing_notices" not in agg["statistics"]
    assert agg["narrative"]["priority_recommendations"][0]["action"] == LEGIBILITY_ONLY


@pytest.mark.asyncio
async def test_a_legacy_aggregate_without_a_notice_recommendation_is_unchanged(tmp_path):
    narrative = _narrative(
        executive_summary="Badanie 2 uczestników. Kalkulator jest trudno dostępny.",
        priority_recommendations=[{"priority": 1, "area": "Nawigacja", "action": "Dodać link do kalkulatora.",
                                   "expected_impact": "Krótsza ścieżka."}])
    agg = await _aggregate(_run_dir(tmp_path, with_facts=False), narrative)
    assert agg["narrative"]["priority_recommendations"] == narrative["priority_recommendations"]
    assert LEFT_OUT_NOTE not in agg["narrative"]["methodology_note"]
    assert agg["narrative"]["executive_summary"] == narrative["executive_summary"]


@pytest.mark.asyncio
async def test_a_stored_aggregate_gets_the_same_rule_when_it_is_rendered_again(tmp_path):
    agg = await _aggregate(_run_dir(tmp_path, with_facts=False), _narrative(
        priority_recommendations=[{"priority": 1, "area": "UI", "action": "Ukryć komunikat UOKiK.",
                                   "expected_impact": ""}]))
    stored = copy.deepcopy(agg)
    stored["narrative"]["priority_recommendations"][0]["action"] = REAL_RECOMMENDATION
    rendered = resolve_narrative(stored)
    assert rendered["priority_recommendations"][0]["action"] == LEGIBILITY_ONLY
    assert resolve_narrative(copy.deepcopy(stored)) == rendered, "rendering twice changes nothing"
    assert rendered["methodology_note"].count(LEFT_OUT_NOTE) == 1


def test_the_note_is_added_when_the_persona_guard_already_took_one_back(tmp_path):
    roll = landing_notice_rollup(_run_dir(tmp_path, left_out=True))
    assert roll["left_out"] == 2
    narr = resolve_narrative({"statistics": {"landing_notices": roll}, "narrative": {
        "executive_summary": "Serwis działa.", "methodology_note": "Badanie.", "priority_recommendations": []}})
    assert narr["methodology_note"].endswith(LEFT_OUT_NOTE)
