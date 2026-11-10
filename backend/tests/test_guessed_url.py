"""A 404 the participant caused by typing an address it made up is not a defect of the site.

2026-11-10 bnpparibas.pl, Marta: on /hipoteka-online/ she typed
https://www.bnpparibas.pl/placowki-i-bankomaty, an address she made up from a menu label.
Nothing on the page linked to it and the site answers 404. One step later she clicked the
real menu item and went on. Yet her blocker said "404 after clicking a link to the
branches", a serious finding "the link leads to a 404" was verified (the quote IS on the
page, which is all the claim verifier checks) and carried the report's whole score
deduction, and the executive summary named a broken link to the branch locator.

The stores below have the shape of that run, with a small crafted page where the real one
was 110 KB: the page she was on (raw_html_k) holds links and none to the address, and the
observation after the typed address is the site's "Strona nie odnaleziona" page.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.agent.prompt_registry import registry
from simulated_web_agent.main import attribution, auditor
from simulated_web_agent.main.recurring import route_summary
from simulated_web_agent.main.reporter import (
    _REFLECTION_SYSTEM, _gate_reason, _human_check, aggregate_statistics, generate_objective_reflection,
)
from simulated_web_agent.main.severity import excluded_from_report, severity_score
from simulated_web_agent.main.trace_store import TraceStore

SITE = "https://www.bnpparibas.pl"
TYPED = f"{SITE}/placowki-i-bankomaty"
FROM_PAGE = f"{SITE}/hipoteka-online/"
NOT_FOUND_TITLE = "Strona nie odnaleziona - BNP Paribas Bank Polska S.A."
# The observation text of the real 404 page, verbatim.
NOT_FOUND_TEXT = ("Bank zmieniającego się świata Placówki i bankomaty Strona nie odnaleziona "
                  "404Nie można znaleźć podanej strony Przepraszamy, strona o podanym adresie nie "
                  "została odnaleziona. Co możesz zrobić?")

# The page she was on: a menu, no link to the typed address.
PAGE_WITHOUT_THE_LINK = (
    "<html><head><title>Hipoteczny - BNP PARIBAS</title></head><body><header><nav><ul>"
    '<li><a href="/kredyt-hipoteczny">Kredyt hipoteczny</a></li>'
    '<li><a href="/hipoteka-online/symulator">Symulator</a></li>'
    '<li><a href="https://www.bnpparibas.pl/kontakt/oddzialy-z-obsluga-detaliczna">Oddziały</a></li>'
    "</ul></nav></header><main>Wniosek w pełni online</main></body></html>")

# Verbatim from the archived run: the finding the claim verifier confirmed, and the blockers.
REAL_EVIDENCE = ("Po kliknięciu odnośnika 'Placówki i bankomaty' użytkownik został przekierowany na "
                 "stronę z błędem 404 (komunikat: 'Strona nie odnaleziona 404 Nie można znaleźć "
                 "podanej strony').")
REAL_FIX = "Naprawić niedziałający link, upewniając się, że prowadzi on do poprawnego adresu wyszukiwarki placówek."
REAL_BLOCKERS = [
    "Brak bezpośredniego odnośnika do symulatora kredytowego na stronie głównej oferty hipotecznej.",
    "Błąd 404 po kliknięciu odnośnika kierującego do placówek i bankomatów.",
    "Wyszukiwarka placówek błędnie interpretuje kody pocztowe, dopasowując je do niepoprawnych ulic w innych miastach.",
]
DESIGN_EVIDENCE = ("Strona błędu 404 pokazuje tylko lakoniczny komunikat 'Strona nie odnaleziona' i nie "
                   "podpowiada, dokąd użytkownik ma przejść; brakuje na niej wyszukiwarki.")


def _obs(url, title, active=True):
    return {"tabs": [{"id": 0, "title": title, "url": url, "is_active": active}], "error": None}


def _marta(tmp_path, *, name="marta", typed=TYPED, page=PAGE_WITHOUT_THE_LINK, after_title=NOT_FOUND_TITLE,
           after_text=NOT_FOUND_TEXT, after_url=None, raw_after=None):
    """Five steps like the run: 0 and 1 on the way in, 2 types the address (the page she was on
    is observation 2), 3 is the 404 page, 4 the real menu item. page=None writes no raw_html.

    The goto is action 2, so the page before it is raw_html_2 and its result observation 3."""
    store = TraceStore(tmp_path / name)
    actions = [
        {"action": "click", "target": "kredyt", "description": "Clicking 'Kredyt hipoteczny'"},
        {"action": "click", "target": "symulator", "description": "Clicking 'Symulator'"},
        {"action": "goto_url", "url": typed, "description":
            "Navigating to 'Placówki i bankomaty' branch locator on the BNP Paribas website"},
        {"action": "click", "target": "plac_wki_i_bankomaty",
         "description": "Clicking on 'Placówki i bankomaty' in the navigation menu"},
    ]
    store.write_action_trace([json.dumps(a) for a in actions])
    pages = [(SITE + "/", "BNP PARIBAS"), (f"{SITE}/kredyt-hipoteczny", "Kredyt hipoteczny"),
             (FROM_PAGE, "Hipoteczny - BNP PARIBAS"),
             (after_url or typed, after_title),
             (f"{SITE}/kontakt/oddzialy-z-obsluga-detaliczna", "Oddziały BNP Paribas")]
    for i, (url, title) in enumerate(pages):
        store.append_observation(_obs(url, title))
        store.write_observation_step_text(i, after_text if i == 3 else f"Strona {title}. Treść strony.")
    if page is not None:
        store.write_raw_html(2, page)
    # what the browser had on the 404 page: the quoted words of a finding are on a page of the session
    store.write_raw_html(3, raw_after if raw_after is not None else
                         f"<html><head><title>{after_title}</title></head><body>{after_text}</body></html>")
    store.write_session_result({"steps_taken": 4, "max_steps": 40, "terminated": True,
                                "ended_reason": "terminated"})
    return store


def _finding(step, evidence, recommendation="Napisać stronę lepiej.", **extra):
    return {"heuristic_id": 5, "severity": "poważny", "step": step, "evidence": evidence,
            "recommendation": recommendation, "verified": True, **extra}


# --- detection ---------------------------------------------------------------------------

def test_an_address_the_page_did_not_link_to_that_loads_an_error_page_is_a_guess(tmp_path):
    assert attribution.guessed_address_steps(_marta(tmp_path)) == {
        3: {"url": TYPED, "action_step": 2, "from_error_page": False}}


@pytest.mark.parametrize("href", [
    "/placowki-i-bankomaty",
    "https://www.bnpparibas.pl/placowki-i-bankomaty/",
    "http://bnpparibas.pl/Placowki-i-bankomaty?utm=menu#top",
    "//www.bnpparibas.pl/placowki-i-bankomaty",
])
def test_an_address_the_page_did_link_to_is_the_sites_own(tmp_path, href):
    """Scheme, www, case, query, fragment and a trailing slash do not tell addresses apart."""
    page = PAGE_WITHOUT_THE_LINK.replace("</ul>", f'<li><a href="{href}">Placówki i bankomaty</a></li></ul>')
    assert attribution.guessed_address_steps(_marta(tmp_path, page=page)) == {}


def test_a_link_in_an_attribute_a_menu_script_reads_counts_as_linked(tmp_path):
    page = PAGE_WITHOUT_THE_LINK.replace("<li>", '<li data-href="/placowki-i-bankomaty">', 1)
    assert attribution.guessed_address_steps(_marta(tmp_path, page=page)) == {}


def test_a_relative_typed_address_is_resolved_against_the_page_it_was_typed_on(tmp_path):
    """The harness resolves it the same way (env.resolve_url), so '/x' is the site's /x."""
    store = _marta(tmp_path, typed="/placowki-i-bankomaty", after_url=TYPED)
    assert attribution.guessed_address_steps(store) == {
        3: {"url": TYPED, "action_step": 2, "from_error_page": False}}
    linked = PAGE_WITHOUT_THE_LINK.replace("</ul>", '<li><a href="/placowki-i-bankomaty">x</a></li></ul>')
    assert attribution.guessed_address_steps(
        _marta(tmp_path, name="linked", typed="/placowki-i-bankomaty", after_url=TYPED, page=linked)) == {}


def test_a_missing_capture_is_unknown_never_a_guess(tmp_path):
    assert attribution.guessed_address_steps(_marta(tmp_path, page=None)) == {}
    assert attribution.guessed_address_steps(_marta(tmp_path, name="blank", page="  \n")) == {}


def test_a_capture_without_a_single_link_is_unknown_too(tmp_path):
    """An unrendered shell says nothing about what the page linked to."""
    store = _marta(tmp_path, page="<html><body><div id='app'></div><a href='#'>x</a></body></html>")
    assert attribution.guessed_address_steps(store) == {}


def test_a_guessed_address_that_loads_an_ordinary_page_changes_nothing(tmp_path):
    store = _marta(tmp_path, after_title="Oddziały BNP Paribas - BNP Paribas Bank Polska S.A.",
                   after_text="Oddziały z obsługą detaliczną. Znajdź placówkę w swojej okolicy. Rata od 404 zł.")
    assert attribution.guessed_address_steps(store) == {}
    assert attribution.guessed_address_note(store) is None
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(store, findings)
    assert not findings[0]["harness_suspect"] and findings[0]["harness_cause"] is None


@pytest.mark.parametrize("title, text", [
    ("Page not found | Example", "Strona główna."),
    ("404", "Coś poszło nie tak."),
    ("Oferta", "Błąd 404. Strona o podanym adresie nie istnieje."),
    ("Oferta", "404Nie można znaleźć podanej strony."),
    ("Oferta", "Przepraszamy, strona nie została odnaleziona (kod 410)."),
])
def test_what_counts_as_an_error_page(tmp_path, title, text):
    store = _marta(tmp_path, after_title=title, after_text=text)
    assert set(attribution.guessed_address_steps(store)) == {3}


@pytest.mark.parametrize("title, text", [
    ("Wyniki wyszukiwania", "Nie znaleziono wyników dla 'abc'."),
    ("Kredyt", "Rata od 404 zł miesięcznie. Najczęstsze pytania: czy konto nie istnieje po zamknięciu?"),
    ("Kredyt 410 000 zł", "Symulator kredytu."),
    # the code and the phrase are too far apart to be one message
    ("Model 404", "Model 404 " + "opis modelu " * 12 + "ta strona nie istnieje w ofercie."),
])
def test_ordinary_pages_that_mention_a_number_or_a_search_miss_are_not_error_pages(tmp_path, title, text):
    assert attribution.guessed_address_steps(_marta(tmp_path, after_title=title, after_text=text)) == {}


def test_an_address_the_participant_had_already_been_to_is_not_a_guess(tmp_path):
    """The start address and every page it saw are in its address bar, linked from here or not."""
    assert 'href="/"' not in PAGE_WITHOUT_THE_LINK
    store = _marta(tmp_path, typed=f"{SITE}/")
    assert attribution.guessed_address_steps(store) == {}
    assert attribution.guessed_address_steps(_marta(tmp_path, name="seen", typed=f"{SITE}/kredyt-hipoteczny")) == {}


def test_a_refused_or_failed_goto_that_left_the_page_where_it_was_is_not_an_error_page_it_reached(tmp_path):
    store = _marta(tmp_path, after_url=FROM_PAGE, after_title=NOT_FOUND_TITLE)
    assert attribution.guessed_address_steps(store) == {}


def test_a_goto_with_no_following_observation_or_url_is_skipped(tmp_path):
    store = TraceStore(tmp_path / "cut")
    store.write_action_trace([json.dumps({"action": "goto_url", "url": TYPED}),
                              json.dumps({"action": "goto_url"})])
    store.append_observation(_obs(FROM_PAGE, "x"))
    assert attribution.guessed_address_steps(store) == {}


def test_a_session_without_a_typed_address_has_nothing_to_find(tmp_path):
    store = TraceStore(tmp_path / "legacy")
    store.write_action_trace([json.dumps({"action": "click", "target": "a", "description": "x"})] * 2)
    for i in range(2):
        store.append_observation(_obs(f"{SITE}/{i}", NOT_FOUND_TITLE))
        store.write_observation_step_text(i, NOT_FOUND_TEXT)
    assert attribution.guessed_address_steps(store) == {}
    assert attribution.guessed_only_dead_ends(store) == {}


def test_the_raw_capture_tells_when_the_tab_title_and_the_perceiver_do_not(tmp_path):
    """The step may have no text at all (the session was cut); the browser's own capture still says."""
    store = _marta(tmp_path, after_title="", after_text="",
                   raw_after=f"<html><head><title>{NOT_FOUND_TITLE}</title></head><body>x</body></html>")
    assert set(attribution.guessed_address_steps(store)) == {3}


# --- findings ----------------------------------------------------------------------------

def test_the_real_finding_is_held_out_of_the_report(tmp_path):
    store = _marta(tmp_path)
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX, url=TYPED)]
    attribution.flag_harness_suspects(store, findings)
    f = findings[0]
    assert f["harness_suspect"] and f["requires_human_check"] and excluded_from_report(f)
    assert f["harness_cause"] == {"code": "guessed_address", "detail": TYPED, "step": 3}
    assert severity_score(f) == 0


def test_the_hold_names_the_reason_and_what_a_person_should_check(tmp_path):
    store = _marta(tmp_path)
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(store, findings)
    reason, check = _gate_reason(findings[0]), _human_check(findings[0])
    assert "w kroku 3" in reason and "sam wpisał adres" in reason and "nie zawierała do niego odnośnika" in reason
    assert "awari" not in reason and "usterk" not in reason  # neither the tool nor the site is blamed
    assert "stronę, z której uczestnik wpisał adres" in check and "placowki-i-bankomaty" in check
    assert "samą stronę błędu" in check


@pytest.mark.parametrize("evidence, recommendation", [
    ("Odnośnik do lokalizatora placówek jest uszkodzony i prowadzi do strony 404.", "Naprawić odnośnik."),
    ("Strona z placówkami nie istnieje (błąd 404).", "Przywrócić stronę."),
    ("Link do lokalizatora kieruje donikąd.", "Poprawić nawigację."),
    ("Po wybraniu pozycji w menu pojawia się strona 'Strona nie odnaleziona'.", "Sprawdzić menu."),
    ("Nawigacja do sekcji placówek kończy się komunikatem Strona nie odnaleziona.", "Naprawić nawigację."),
    ("Kliknięcie zakładki Placówki skutkuje błędem 404.", "Sprawdzić zakładkę."),
    ("Strona jest niedostępna.", "Naprawić niedziałający link do placówek."),
])
def test_a_claim_that_blames_the_site_for_the_dead_address_is_held(tmp_path, evidence, recommendation):
    store = _marta(tmp_path)
    findings = [_finding(3, evidence, recommendation)]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_cause"]["code"] == "guessed_address", evidence


def test_it_is_held_when_cited_at_the_step_the_address_was_typed(tmp_path):
    findings = [_finding(2, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(_marta(tmp_path), findings)
    assert findings[0]["harness_cause"] == {"code": "guessed_address", "detail": TYPED, "step": 3}


@pytest.mark.parametrize("evidence, recommendation", [
    (DESIGN_EVIDENCE, "Dodać na stronie błędu wyszukiwarkę oraz odnośniki do głównych działów."),
    ("Komunikat 'Strona nie odnaleziona 404' jest zbyt lakoniczny i nie wskazuje, dokąd przejść.",
     "Rozbudować treść komunikatu."),
    ("Na stronie błędu 404 brakuje menu głównego, więc użytkownik nie wie, co dalej.", "Dodać menu."),
    ("Strona błędu 404 jest wizualnie uboga i nie zawiera linków do działów.",
     "Dodać linki prowadzące do głównych działów serwisu."),
    ("Strona błędu używa małej czcionki i niskiego kontrastu.", "Poprawić kontrast."),
    # the page's own words are what the page says, not a claim about the site
    ("Użytkownik widzi wyłącznie hasło 'Strona nie odnaleziona 404'.", "Rozbudować stronę."),
    ("Komunikat informuje, że strona o podanym adresie nie została odnaleziona, ale nie proponuje "
     "wyszukiwarki.", "Dodać wyszukiwarkę."),
])
def test_a_finding_about_the_error_pages_own_design_is_not_held(tmp_path, evidence, recommendation):
    """What the 404 page shows or lacks is the site's to answer for, whoever got there."""
    store = _marta(tmp_path)
    findings = [_finding(3, evidence, recommendation)]
    attribution.flag_harness_suspects(store, findings)
    f = findings[0]
    assert not f["harness_suspect"] and not excluded_from_report(f) and f["harness_cause"] is None, evidence
    assert severity_score(f) == 3


def test_a_finding_about_something_else_on_the_step_is_not_held(tmp_path):
    store = _marta(tmp_path)
    findings = [_finding(3, "Wyszukiwarka placówek błędnie interpretuje adres jako ulicę w Warszawie.")]
    attribution.flag_harness_suspects(store, findings)
    assert not findings[0]["harness_suspect"]


def test_a_blaming_claim_on_another_step_is_left_alone(tmp_path):
    """The hold is tied to the page the participant made, not to every mention of a 404."""
    store = _marta(tmp_path)
    findings = [_finding(4, REAL_EVIDENCE, REAL_FIX), _finding(0, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(store, findings)
    assert not any(f["harness_suspect"] for f in findings)


def test_the_same_claim_about_an_address_the_page_did_link_to_stays_as_it_is(tmp_path):
    """Then the dead end is the site's own link, and the finding is the site's."""
    linked = PAGE_WITHOUT_THE_LINK.replace("</ul>", '<li><a href="/placowki-i-bankomaty">Placówki</a></li></ul>')
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(_marta(tmp_path, page=linked), findings)
    f = findings[0]
    assert not f["harness_suspect"] and f["harness_cause"] is None and severity_score(f) == 3


def test_a_missing_capture_leaves_the_finding_as_it_is(tmp_path):
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(_marta(tmp_path, page=None), findings)
    assert not findings[0]["harness_suspect"]


def test_the_flags_are_rewritten_when_the_trace_changes(tmp_path):
    """Idempotent like the rest of the gate: no hold left latched on once the page links to it."""
    store = _marta(tmp_path)
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"]
    store.write_raw_html(2, PAGE_WITHOUT_THE_LINK.replace(
        "</ul>", '<li><a href="/placowki-i-bankomaty">x</a></li></ul>'))
    attribution.flag_harness_suspects(store, findings)
    assert not findings[0]["harness_suspect"] and findings[0]["harness_cause"] is None


def test_a_legacy_trace_without_any_marker_flags_exactly_as_before(tmp_path):
    """No goto_url and no marker anywhere: the new rule finds nothing to do."""
    store = TraceStore(tmp_path / "legacy")
    store.write_action_trace([json.dumps({"action": "click", "target": "a", "description": "x"})] * 2)
    for i in range(2):
        store.append_observation(_obs(f"{SITE}/{i}", f"Strona {i}"))
        store.write_observation_step_text(i, f"Strona {i}")
    findings = [_finding(1, REAL_EVIDENCE, REAL_FIX), _finding(0, "Strona nie podaje składu materiału.")]
    attribution.flag_harness_suspects(store, findings)
    assert not any(f["harness_suspect"] or f["requires_human_check"] for f in findings)
    assert [f["harness_cause"] for f in findings] == [None, None]


# --- what the passes written from the trace are told -----------------------------------------

async def _heuristic_prompt(store) -> str:
    chat = AsyncMock(return_value="[]")
    with patch("simulated_web_agent.main.auditor.async_chat", new=chat):
        await auditor._heuristic_pass(store)
    return chat.call_args.args[0][0]["content"]


def _actions_section(prompt: str) -> str:
    return prompt.split("ACTIONS:\n")[1].split("\n\n## Output format")[0]


@pytest.mark.asyncio
async def test_the_auditor_is_told_the_participant_typed_the_address(tmp_path):
    prompt = await _heuristic_prompt(_marta(tmp_path))
    actions = prompt.split("ACTIONS:\n")[1].split("\n\n## Output format")[0].splitlines()
    typed = next(line for line in actions if "[step 2]" in line)
    assert "ADDRESS TYPED BY THE PARTICIPANT" in typed and "had no link to it" in typed
    assert "says nothing about the site's links" in typed
    assert sum("ADDRESS TYPED BY THE PARTICIPANT" in line for line in actions) == 1
    assert "[step 3] https://www.bnpparibas.pl/placowki-i-bankomaty :: " in prompt  # the page itself is still shown


@pytest.mark.asyncio
async def test_the_auditor_marks_nothing_for_an_address_the_page_linked_to_or_a_normal_page(tmp_path):
    linked = PAGE_WITHOUT_THE_LINK.replace("</ul>", '<li><a href="/placowki-i-bankomaty">x</a></li></ul>')
    assert "ADDRESS TYPED" not in _actions_section(await _heuristic_prompt(_marta(tmp_path, page=linked)))
    ordinary = _marta(tmp_path, name="ordinary", after_title="Oddziały", after_text="Oddziały banku.")
    assert "ADDRESS TYPED" not in _actions_section(await _heuristic_prompt(ordinary))


def test_the_marker_says_nothing_the_gates_read_as_a_claim_about_the_site():
    from simulated_web_agent.main.attribution import _ABSENCE_MARKERS as ATTRIBUTION_ABSENCE
    from simulated_web_agent.main.claim_verifier import _ABSENCE_MARKERS, _BROKEN_MARKERS
    marker = auditor._TYPED_ADDRESS_MARKER.lower()
    for word in (*ATTRIBUTION_ABSENCE, *_ABSENCE_MARKERS, *_BROKEN_MARKERS):
        assert word not in marker, word
    assert not attribution.text_blames_dead_address(marker)


def test_the_heuristic_prompt_explains_the_marker_and_still_formats():
    prompt = registry.get("heuristic_evaluate")
    assert "ADDRESS TYPED BY THE PARTICIPANT" in prompt and "must never be reported as one" in prompt
    filled = prompt.format(site_context="s", persona_disposition="p", session_facts="f",
                           observations="o", reflections="r", actions="a")
    assert "ADDRESS TYPED BY THE PARTICIPANT" in filled


REPLY = json.dumps({"outcome": "success", "summary": "x", "blockers": [], "wins": [], "priorityFix": ""})


async def _reflection_payload(store, reply=REPLY) -> dict:
    chat = AsyncMock(return_value=reply)
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        await generate_objective_reflection(store)
    return json.loads(chat.call_args.args[0][1]["content"])


@pytest.mark.asyncio
async def test_the_reflection_model_is_told_the_participant_typed_the_address(tmp_path):
    payload = await _reflection_payload(_marta(tmp_path))
    note = payload["typed_address_note"]
    assert note.startswith("W kroku 2 uczestnik sam wpisał adres") and "placowki-i-bankomaty" in note
    assert "nie zawierała do niego odnośnika" in note and "strona błędu w kroku 3" in note
    assert '"typed_address_note"' in _REFLECTION_SYSTEM
    assert "nie wpisuj tego do blockers ani do priorityFix" in _REFLECTION_SYSTEM


@pytest.mark.asyncio
async def test_a_session_without_a_typed_address_gets_no_note(tmp_path):
    ordinary = _marta(tmp_path, name="ordinary", after_title="Oddziały", after_text="Oddziały banku.")
    assert (await _reflection_payload(ordinary))["typed_address_note"] is None


@pytest.mark.asyncio
async def test_the_held_finding_is_not_handed_to_the_reflection_model(tmp_path):
    store = _marta(tmp_path)
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(store, findings)
    store.write_heuristic_findings(findings)
    assert (await _reflection_payload(store))["heuristic_findings"] == []


@pytest.mark.asyncio
async def test_a_blocker_that_blames_the_site_for_the_dead_address_is_taken_out_of_the_stored_reflection(tmp_path):
    store = _marta(tmp_path)
    reply = json.dumps({
        "outcome": "success",
        "summary": "Marta zrealizowała cel. Napotkała błąd 404 po kliknięciu odnośnika do placówek. "
                   "Znalazła placówkę w Krakowie.",
        "blockers": REAL_BLOCKERS, "wins": ["Czytelny symulator."],
        "priorityFix": "Naprawić uszkodzony link do lokalizatora placówek."})
    await _reflection_payload(store, reply)
    saved = store.read_objective_reflection()
    assert saved["blockers"] == [REAL_BLOCKERS[0], REAL_BLOCKERS[2]]
    assert saved["summary"] == "Marta zrealizowała cel. Znalazła placówkę w Krakowie."
    assert saved["priorityFix"] == "" and saved["wins"] == ["Czytelny symulator."]


# --- the aggregate report ----------------------------------------------------------------

def _report_for(store, findings):
    return {"trace_dir": str(store.trace_dir), "persona": "Persona: Marta", "intent": "Sprawdź kredyt.",
            "first_impression": {}, "heuristic_findings": findings,
            "accessibility": {"findings": []}, "survey_result": {"answers": []},
            "session_result": store.read_session_result()}


def _with_reflection(store, blockers=None, summary="Marta zrealizowała cel.", fix="Dodać odnośnik do symulatora."):
    store.write_objective_reflection({
        "outcome": "success", "summary": summary,
        "blockers": REAL_BLOCKERS if blockers is None else blockers,
        "wins": ["Czytelny symulator."], "priorityFix": fix})
    return store


def test_the_held_finding_leaves_the_counts_and_is_listed_for_verification(tmp_path):
    store = _marta(tmp_path)
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX),
                _finding(0, "Strona główna nie pokazuje stawki RRSO.", heuristic_id=2, severity="drobny")]
    attribution.flag_harness_suspects(store, findings)
    stats = aggregate_statistics([_report_for(store, findings)], stores=[store])
    counts = stats["heuristic"]["severity_counts"]
    assert counts["poważny"] == 0 and counts["drobny"] == 1 and counts["needs_verification"] == 1
    (item,) = stats["heuristic"]["needs_verification_items"]
    assert "sam wpisał adres" in item["reason"] and "stronę, z której uczestnik wpisał adres" in item["check"]
    # a 404 is page content, never the pointer-events error that repeated_obstacles counts
    assert stats["repeated_obstacles"] == []


def test_the_design_finding_on_the_error_page_still_counts(tmp_path):
    store = _marta(tmp_path)
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX), _finding(3, DESIGN_EVIDENCE, "Dodać wyszukiwarkę.",
                                                                  heuristic_id=9)]
    attribution.flag_harness_suspects(store, findings)
    counts = aggregate_statistics([_report_for(store, findings)], stores=[store])["heuristic"]["severity_counts"]
    assert counts["poważny"] == 1 and counts["needs_verification"] == 1


def test_the_blocker_that_cites_the_404_does_not_reach_the_narrator(tmp_path):
    store = _with_reflection(_marta(tmp_path), summary=(
        "Marta w pełni zrealizowała cel. Błąd 404 po kliknięciu odnośnika uniemożliwił jej dojście do "
        "placówek. Znalazła ostatecznie lokalizator."))
    (row,) = aggregate_statistics([_report_for(store, [])], stores=[store])["objective_reflections"]
    assert row["blockers"] == [REAL_BLOCKERS[0], REAL_BLOCKERS[2]]
    assert row["summary"] == "Marta w pełni zrealizowała cel. Znalazła ostatecznie lokalizator."
    assert row["wins"] == ["Czytelny symulator."] and row["priority_fix"] == "Dodać odnośnik do symulatora."
    assert "404" not in " ".join(row["blockers"]) + row["summary"]


def test_a_fix_that_asks_for_the_link_to_be_repaired_is_blanked(tmp_path):
    store = _with_reflection(_marta(tmp_path), fix="Naprawić niedziałający link do lokalizatora placówek.")
    (row,) = aggregate_statistics([_report_for(store, [])], stores=[store])["objective_reflections"]
    assert row["priority_fix"] == ""


def test_a_remark_about_the_error_page_itself_stays_in_the_account(tmp_path):
    remark = "Strona błędu 404 nie podpowiada, dokąd przejść, i nie ma wyszukiwarki."
    store = _with_reflection(_marta(tmp_path), blockers=[remark, REAL_BLOCKERS[0]])
    (row,) = aggregate_statistics([_report_for(store, [])], stores=[store])["objective_reflections"]
    assert row["blockers"] == [remark, REAL_BLOCKERS[0]]


def test_the_route_the_narrator_reads_says_who_typed_the_address(tmp_path):
    route = route_summary(_marta(tmp_path))
    assert route[2] == ("2: Navigating to 'Placówki i bankomaty' branch locator on the BNP Paribas "
                        "website (adres wpisany przez uczestnika, strona go nie podawała)")
    assert "adres wpisany" not in " ".join(route[:2] + route[3:])
    linked = PAGE_WITHOUT_THE_LINK.replace("</ul>", '<li><a href="/placowki-i-bankomaty">x</a></li></ul>')
    assert not any("adres wpisany" in step for step in route_summary(_marta(tmp_path, name="l", page=linked)))


def test_a_dead_end_the_participant_did_not_make_keeps_its_blockers(tmp_path):
    """Two error pages in one session: a complaint about a 404 could be about the site's own."""
    store = _marta(tmp_path)
    store.append_observation(_obs(f"{SITE}/stara-oferta", "Strona nie odnaleziona - BNP"))
    store.write_observation_step_text(5, "Strona nie odnaleziona 404")
    assert attribution.guessed_only_dead_ends(store) == {}
    _with_reflection(store)
    (row,) = aggregate_statistics([_report_for(store, [])], stores=[store])["objective_reflections"]
    assert row["blockers"] == REAL_BLOCKERS


def test_a_session_without_a_typed_address_keeps_every_blocker(tmp_path):
    ordinary = _with_reflection(_marta(tmp_path, name="ordinary", after_title="Oddziały",
                                       after_text="Oddziały banku."))
    (row,) = aggregate_statistics([_report_for(ordinary, [])], stores=[ordinary])["objective_reflections"]
    assert row["blockers"] == REAL_BLOCKERS and row["summary"] == "Marta zrealizowała cel."


# --- the real run, as recorded -------------------------------------------------------------

def test_the_bnp_run_end_to_end(tmp_path):
    """The three false statements of the real report, from the facts of the run: the finding is
    held, the blocker is dropped, and the summary has nothing to build 'a broken link to the
    branch locator' on."""
    store = _with_reflection(_marta(tmp_path))
    findings = [
        _finding(0, "Na stronie oferty brakuje widocznego odnośnika do symulatora.", heuristic_id=6,
                 severity="drobny"),
        _finding(3, REAL_EVIDENCE, REAL_FIX, url=TYPED),
    ]
    attribution.flag_harness_suspects(store, findings)
    assert [f["harness_suspect"] for f in findings] == [False, True]
    stats = aggregate_statistics([_report_for(store, findings)], stores=[store])
    assert stats["heuristic"]["severity_counts"]["poważny"] == 0
    (row,) = stats["objective_reflections"]
    assert not any("404" in b for b in row["blockers"])
    assert "adres wpisany przez uczestnika" in " ".join(row["route"])


# --- the agent's own prompts ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["action", "feedback"])
def test_the_agent_prompts_are_sent_as_written_so_carry_no_format_escapes(name):
    """These two go to the model as they are (no str.format): a doubled brace would reach it
    literally. The audit prompts are formatted and keep theirs (the test above)."""
    prompt = registry.get(name)
    assert "{{" not in prompt and "}}" not in prompt
    assert '{ "action": "goto_url", "url": "https://example.com"' in prompt


def test_the_action_prompt_limits_goto_url_to_addresses_seen_on_the_site():
    prompt = " ".join(registry.get("action").split())
    assert "Use `goto_url` only for an address I have seen on the site" in prompt
    assert "a link on the page, the address bar of a page I was on, or the start address" in prompt
    assert "Never invent or guess an address from a page title or a menu label" in prompt
    assert "to reach a section, click its menu or footer link" in prompt


def test_the_feedback_prompt_does_not_let_the_participant_call_its_own_address_a_broken_link():
    prompt = " ".join(registry.get("feedback").split())
    assert "the address was one I typed myself" in prompt
    assert "do not write that a link is broken or that a page is missing from the site" in prompt


# --- where the detection and the blame test stop ---------------------------------------------

NOT_FOUND_PAGE = (f"<html><head><title>{NOT_FOUND_TITLE}</title></head><body>{NOT_FOUND_TEXT}"
                  '<a href="/kontakt">Kontakt</a></body></html>')


def _page(*links):
    return ("<html><head><title>Strona</title></head><body>"
            + "".join(f'<a href="{href}">x</a>' for href in links) + "</body></html>")


def _session(tmp_path, name, actions, pages, raws):
    """pages: (url, title, text) for each observation; raws: {step: markup}."""
    store = TraceStore(tmp_path / name)
    store.write_action_trace([json.dumps(a) for a in actions])
    for i, (url, title, text) in enumerate(pages):
        store.append_observation(_obs(url, title))
        store.write_observation_step_text(i, text)
    for step, markup in raws.items():
        store.write_raw_html(step, markup)
    return store


def _goto(url):
    return {"action": "goto_url", "url": url, "description": "Navigating"}


def _click(label):
    return {"action": "click", "target": label, "description": f"Clicking '{label}'"}


@pytest.mark.parametrize("typed", ["https://[domena]/x", "http://[::1", "https://[a/b"])
@pytest.mark.asyncio
async def test_a_placeholder_the_model_wrote_is_no_address_and_breaks_no_pass(tmp_path, typed):
    """textutil.view_key says such placeholders occur; urlparse raises on them, and the
    exception left the audit pass, the flagging and the reflection of that persona."""
    store = _marta(tmp_path, typed=typed, after_url=TYPED)
    assert attribution.guessed_address_steps(store) == {}
    assert attribution.guessed_only_dead_ends(store) == {} and attribution.guessed_address_note(store) is None
    findings = [_finding(3, REAL_EVIDENCE, REAL_FIX)]
    attribution.flag_harness_suspects(store, findings)
    assert not findings[0]["harness_suspect"]
    assert "ADDRESS TYPED" not in _actions_section(await _heuristic_prompt(store))
    assert (await _reflection_payload(store))["typed_address_note"] is None
    assert route_summary(store)


def _dead_link_then_guess(tmp_path):
    """The site's own link leads to a 404 (obs 1); from that page the participant types an
    address of its own, which is a 404 too (obs 2)."""
    return _session(
        tmp_path, "real_then_guess", [_click("stary_link"), _goto(TYPED)],
        [(f"{SITE}/", "BNP PARIBAS", "Strona główna."),
         (f"{SITE}/stary-link", NOT_FOUND_TITLE, NOT_FOUND_TEXT),
         (TYPED, NOT_FOUND_TITLE, NOT_FOUND_TEXT)],
        {0: _page("/stary-link", "/kontakt"), 1: NOT_FOUND_PAGE, 2: NOT_FOUND_PAGE})


def test_a_guess_typed_on_an_error_page_says_so(tmp_path):
    guessed = attribution.guessed_address_steps(_dead_link_then_guess(tmp_path))
    assert guessed == {2: {"url": TYPED, "action_step": 1, "from_error_page": True}}


def test_a_dead_link_of_the_site_is_not_held_because_a_guess_was_typed_on_its_error_page(tmp_path):
    """Step 1 is the site's own 404; the participant typing something there afterwards does
    not make the link that led to it the participant's doing."""
    store = _dead_link_then_guess(tmp_path)
    real = "Po kliknięciu odnośnika 'Stary link' użytkownik trafia na stronę błędu 404."
    findings = [_finding(1, real, "Naprawić niedziałający link."), _finding(2, real, "Naprawić niedziałający link.")]
    attribution.flag_harness_suspects(store, findings)
    assert not findings[0]["harness_suspect"] and findings[0]["harness_cause"] is None
    assert findings[1]["harness_cause"]["code"] == "guessed_address"  # the page the guess itself made
    assert attribution.guessed_only_dead_ends(store) == {}  # the same session keeps the blockers


def _earlier_page_links(tmp_path, a_links, name):
    """A links to /dead (or not), the participant clicks on to B, which does not, and types /dead."""
    return _session(
        tmp_path, name, [_click("b"), _goto(f"{SITE}/dead")],
        [(f"{SITE}/a", "Strona A", "Strona A."), (f"{SITE}/b", "Strona B", "Strona B."),
         (f"{SITE}/dead", NOT_FOUND_TITLE, NOT_FOUND_TEXT)],
        {0: _page(*a_links), 1: _page("/a", "/kontakt"), 2: NOT_FOUND_PAGE})


def test_an_address_an_earlier_page_linked_to_is_the_sites_own(tmp_path):
    """The site did link to it, from a page the participant had left: a dead link of the site."""
    linked = _earlier_page_links(tmp_path, ("/b", "/dead"), "linked")
    assert attribution.guessed_address_steps(linked) == {}
    findings = [_finding(2, "Link 'Dead' na stronie A prowadzi do błędu 404.", "Naprawić link.")]
    attribution.flag_harness_suspects(linked, findings)
    assert not findings[0]["harness_suspect"]
    # the same session when no page linked to it is the guess it looks like
    assert set(attribution.guessed_address_steps(_earlier_page_links(tmp_path, ("/b",), "unlinked"))) == {2}


def test_typing_the_same_made_up_address_again_is_a_second_guess(tmp_path):
    """The 404 page the first guess showed has that address in its bar; that is not having been there."""
    store = _session(
        tmp_path, "twice", [_goto(TYPED), {"action": "back", "description": "Going back"}, _goto(TYPED)],
        [(FROM_PAGE, "Hipoteczny", "Hipoteczny."), (TYPED, NOT_FOUND_TITLE, NOT_FOUND_TEXT),
         (FROM_PAGE, "Hipoteczny", "Hipoteczny."), (TYPED, NOT_FOUND_TITLE, NOT_FOUND_TEXT)],
        {0: PAGE_WITHOUT_THE_LINK, 1: NOT_FOUND_PAGE, 2: PAGE_WITHOUT_THE_LINK, 3: NOT_FOUND_PAGE})
    assert set(attribution.guessed_address_steps(store)) == {1, 3}
    assert set(attribution.guessed_only_dead_ends(store)) == {1, 3}


def test_an_address_that_showed_an_error_page_after_a_real_click_is_still_one_the_participant_had_seen(tmp_path):
    store = _session(
        tmp_path, "seen", [_click("x"), {"action": "back", "description": "Going back"}, _goto(f"{SITE}/x")],
        [(f"{SITE}/", "BNP PARIBAS", "Strona główna."), (f"{SITE}/x", NOT_FOUND_TITLE, NOT_FOUND_TEXT),
         (f"{SITE}/", "BNP PARIBAS", "Strona główna."), (f"{SITE}/x", NOT_FOUND_TITLE, NOT_FOUND_TEXT)],
        {0: _page("/kontakt"), 1: NOT_FOUND_PAGE, 2: _page("/kontakt"), 3: NOT_FOUND_PAGE})
    assert attribution.guessed_address_steps(store) == {}


# What is said about a step of the typed address, and is not about its dead end: a price that
# happens to be 404 or 410, a thing missing on a page, a link that is out of date.
OTHER_CLAIMS = [
    "Rata kredytu 410 zł miesięcznie jest wyświetlona bez objaśnienia, z czego się składa.",
    "Suwak kwoty pozwala wybrać maksymalnie 404 000 zł, ale nie podaje limitu dochodowego.",
    "Rata wynosi 404,50 zł, ale strona nie tłumaczy jej składników.",
    "Na stronie nie istnieje widoczna informacja o prowizji.",
    "Opcja darmowego konta nie istnieje w ofercie, a strona o tym nie informuje.",
    "Link do regulaminu jest nieaktualny i wprowadza użytkownika w błąd.",
]
# On the page the address was typed from, not on the error page that followed it.
OTHER_CLAIMS_BEFORE = [
    "Po kliknięciu 'Wyślij' pojawia się błąd walidacji formularza.",
    "Po kliknięciu 'Szukaj' nie znaleziono wyników.",
    "Link do polityki prywatności jest uszkodzony.",
]


@pytest.mark.parametrize("step, claim", [(3, c) for c in OTHER_CLAIMS] + [(2, c) for c in OTHER_CLAIMS + OTHER_CLAIMS_BEFORE])
def test_a_finding_about_something_else_on_the_typed_address_steps_is_not_held(tmp_path, step, claim):
    findings = [_finding(step, claim, "Poprawić to.")]
    attribution.flag_harness_suspects(_marta(tmp_path), findings)
    f = findings[0]
    assert not f["harness_suspect"] and f["harness_cause"] is None and not excluded_from_report(f), claim


@pytest.mark.parametrize("claim", [
    "Po kliknięciu 'Oblicz' rata wynosi 410 zł miesięcznie.",
    "Link do oferty prowadzi do raty 404,50 zł.",
    "Odnośnik kieruje do kwoty 410 000 zł.",
    "Po przejściu do symulatora widać kwotę 404 000 zł.",
])
def test_a_price_after_a_click_is_not_an_error_code_even_on_the_error_page_step(claim):
    """The finding cites the error page itself, so only the wording decides; a figure is not a 404."""
    assert not attribution.finding_blames_dead_address({"step": 3, "evidence": claim, "recommendation": claim})


def test_on_the_page_the_address_was_typed_from_the_claim_must_name_the_dead_end(tmp_path):
    """The model often cites the step before the error page; a broken link is then the
    participant's own only if the claim names the error, the address or the click that led to it."""
    named = ("Link do lokalizatora jest uszkodzony, a adres /placowki-i-bankomaty nie działa.",
             "Odnośnik do lokalizatora prowadzi do strony 404.", "Link do lokalizatora kieruje donikąd.")
    for i, claim in enumerate(named):
        findings = [_finding(2, claim, "Naprawić to.")]
        attribution.flag_harness_suspects(_marta(tmp_path, name=f"named{i}"), findings)
        assert findings[0]["harness_cause"]["code"] == "guessed_address", claim


@pytest.mark.parametrize("text", [
    "Marta obliczyła ratę 410 zł miesięcznie i przeszła dalej.",
    "Rata wynosi 404,50 zł.",
    "Wkład własny 410 000 zł.",
    "Opcja darmowego konta nie istnieje w ofercie.",
    "Na stronie nie istnieje informacja o prowizji.",
    "Po kliknięciu 'Wyślij' pojawia się błąd walidacji formularza.",
    "Po kliknięciu 'Szukaj' nie znaleziono wyników.",
    "Link do regulaminu jest nieaktualny.",
])
def test_free_text_about_something_else_is_not_cleaned(text):
    assert not attribution.text_blames_dead_address(text)
    assert attribution.without_dead_address_blame(text) == text


@pytest.mark.parametrize("text", [
    "Błąd 404 po kliknięciu odnośnika kierującego do placówek i bankomatów.",
    "Strona z placówkami nie istnieje.",
    "Podstrona z placówkami nie została odnaleziona.",
    "Po kliknięciu odnośnika użytkownik trafia na stronę z błędem.",
    "Po kliknięciu odnośnika do placówek pojawia się strona nie odnaleziona.",
    "Naprawić uszkodzony link do lokalizatora placówek.",
    "Link do lokalizatora kieruje donikąd.",
])
def test_free_text_that_blames_the_dead_address_is_still_cleaned(text):
    assert attribution.text_blames_dead_address(text), text
    assert attribution.without_dead_address_blame(text) == ""


def test_the_account_of_a_session_with_a_typed_address_keeps_what_is_not_about_it(tmp_path):
    kept = ["Opcja darmowego konta nie istnieje w ofercie.", "Rata 404,50 zł jest podana bez objaśnienia.",
            "Po kliknięciu 'Wyślij' pojawia się błąd walidacji."]
    store = _with_reflection(_marta(tmp_path), blockers=[*kept, REAL_BLOCKERS[1]],
                             summary="Marta obliczyła ratę 410 zł miesięcznie i przeszła dalej. Znalazła symulator.")
    (row,) = aggregate_statistics([_report_for(store, [])], stores=[store])["objective_reflections"]
    assert row["blockers"] == kept
    assert row["summary"] == "Marta obliczyła ratę 410 zł miesięcznie i przeszła dalej. Znalazła symulator."
