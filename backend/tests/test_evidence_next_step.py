"""A finding that cites the step of a typed action is judged against the page that action loaded.

2026-11-10 bnpparibas.pl, Marta: at step 22 she typed '31-147 Kraków' into the branch locator
and the site sent her to 'Krakowskie Przedmieście 31-147, Warszawa'. The finding was true and
was held out of the report as "zapis sesji nie potwierdza tego stwierdzenia": it cited step 22,
the step of the typed action, and the claim verifier read only observation 22. The Warsaw page
is observation 23 — observation k+1 is the result of action k. In earlier regenerations the
model cited 23 and the same finding passed.

The stores below have that shape in small: action 2 types, observation 3 holds the result.
"""
import json
from unittest.mock import AsyncMock, patch

from simulated_web_agent.main import attribution
from simulated_web_agent.main.claim_verifier import run_claim_verification
from simulated_web_agent.main.evidence import following_observation_step, resolve_evidence
from simulated_web_agent.main.severity import excluded_from_report
from simulated_web_agent.main.trace_store import TraceStore

SITE = "https://www.bnpparibas.pl"
QUOTE = "Krakowskie Przedmieście 31-147, Warszawa"
CLAIM = (f"Po wpisaniu '31-147 Kraków' wyszukiwarka placówek wskazała '{QUOTE}' "
         "zamiast placówki w Krakowie.")


def _store(tmp_path, texts, *, n_actions=None, name="marta"):
    """Observations 0..len(texts)-1 with the given text; action i types or clicks on page i."""
    store = TraceStore(tmp_path / name)
    n_actions = len(texts) - 1 if n_actions is None else n_actions
    store.write_action_trace([
        json.dumps({"action": "type", "target": "szukaj", "description": f"Typing in the locator ({i})"})
        for i in range(n_actions)])
    for i, text in enumerate(texts):
        store.append_observation({"tabs": [{"url": f"{SITE}/p{i}", "is_active": True}]})
        if text is not None:
            store.write_observation_step_text(i, text)
    return store


def _marta(tmp_path, **kw):
    # 0 landing, 1 locator, 2 the page she typed on, 3 the result (Warsaw), 4 later
    texts = ["Strona główna", "Wyszukiwarka placówek", "Pole 'Szukaj placówki'", f"Wynik: {QUOTE}", "Dalej"]
    return _store(tmp_path, kw.pop("texts", texts), **kw)


def _f(step, **kw):
    base = {"heuristic_id": 2, "severity": "poważny", "step": step, "url": f"{SITE}/p{step}",
            "evidence": CLAIM, "confidence": "high", "verified": True,
            "harness_suspect": False, "off_site": False}
    return {**base, **kw}


def _judge(reply_evidence="after_action"):
    """A verifier that reads only what it is given: supported when the quoted page text is in
    an observation of the payload, unsupported otherwise."""
    async def chat(messages, **_):
        prompt = messages[0]["content"]
        payload = json.loads(prompt[prompt.index("CLAIMS:") + len("CLAIMS:"):prompt.index("## Verdict rules")])
        verdicts = []
        for c in payload:
            seen = (c.get("observation_full", ""),
                    c.get("observation_after_action_full", "") + c.get("url_after_action", ""))
            ok = any(QUOTE in s for s in seen)
            via = "after_action" if QUOTE in seen[1] else "cited"
            verdicts.append({"id": c["id"], "verdict": "supported" if ok else "unsupported",
                             "reason": "powód", "evidence": via if reply_evidence else "cited"})
        return json.dumps({"verdicts": verdicts})
    return AsyncMock(side_effect=chat)


def _claims_of(mock):
    prompt = mock.call_args.args[0][0]["content"]
    return prompt[prompt.index("CLAIMS:"):prompt.index("## Verdict rules")]


async def _verify(store, findings):
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=_judge()):
        await run_claim_verification(store, findings)
    return findings[0]


# --- the claim verifier ------------------------------------------------------

async def test_citing_the_typed_action_step_verifies_against_the_result_page(tmp_path):
    f = await _verify(_marta(tmp_path), [_f(2)])
    assert f["claim_verdict"] == "supported"
    assert excluded_from_report(f) is False
    assert f["step"] == 2          # as cited
    assert f["evidence_step"] == 3  # the page the evidence was on


async def test_citing_the_result_step_verifies_without_an_evidence_step(tmp_path):
    f = await _verify(_marta(tmp_path), [_f(3)])
    assert f["claim_verdict"] == "supported"
    assert excluded_from_report(f) is False
    assert "evidence_step" not in f


async def test_evidence_in_the_address_the_action_loaded_counts(tmp_path):
    # Marta's case: the Warsaw address is in the result page's URL (percent-encoded)
    store = _marta(tmp_path, texts=["a", "b", "c", "Lista placówek", "d"])
    obs = store.read_observations()
    obs[3]["tabs"][0]["url"] = f"{SITE}/oddzialy?address=Krakowskie%20Przedmieście%2031-147,%20Warszawa"
    (store.trace_dir / "observation_trace.jsonl").write_text(
        "\n".join(json.dumps(o) for o in obs) + "\n", encoding="utf-8")
    f = await _verify(store, [_f(2)])
    assert f["claim_verdict"] == "supported" and f["evidence_step"] == 3


async def test_quote_in_neither_observation_stays_held(tmp_path):
    store = _marta(tmp_path, texts=["a", "b", "c", "Wynik: coś innego", "d"])
    f = await _verify(store, [_f(2)])
    assert f["claim_verdict"] == "unsupported"
    assert excluded_from_report(f) is True
    assert "evidence_step" not in f


async def test_quote_only_two_steps_on_stays_held(tmp_path):
    store = _marta(tmp_path, texts=["a", "b", "c", "d", f"Wynik: {QUOTE}"], n_actions=5)
    f = await _verify(store, [_f(2)])
    assert f["claim_verdict"] == "unsupported"
    assert excluded_from_report(f) is True


async def test_missing_following_observation_stays_held(tmp_path):
    # the cited step is the last observation: there is no k+1 to read
    store = _marta(tmp_path, texts=["a", "b", "c"], n_actions=3)
    assert following_observation_step(store, 2) is None
    f = await _verify(store, [_f(2)])
    assert f["claim_verdict"] == "unsupported"


async def test_unperceived_following_observation_is_never_used(tmp_path):
    # a session cut inside the perceive call: observation 3 was captured, never read (no text)
    store = _marta(tmp_path, texts=["a", "b", "c", None], n_actions=3)
    assert attribution.unperceived_steps(store) == [3]
    assert following_observation_step(store, 2) is None
    f = await _verify(store, [_f(2)])
    assert f["claim_verdict"] == "unsupported"


async def test_following_observation_is_labelled_in_the_prompt(tmp_path):
    mock = _judge()
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(_marta(tmp_path), [_f(2)])
    claims = _claims_of(mock)
    assert "observation_after_action_full" in claims
    assert claims.count(QUOTE) == 2  # the claim text and the next observation; not the cited one


async def test_legacy_payload_without_a_following_page_is_unchanged(tmp_path):
    mock = _judge()
    store = _marta(tmp_path, texts=["a", "b", "c"], n_actions=3)
    with patch("simulated_web_agent.main.claim_verifier.async_chat", new=mock):
        await run_claim_verification(store, [_f(2)])
    assert "observation_after_action_full" not in _claims_of(mock)


# --- the evidence resolver ---------------------------------------------------

def test_resolve_evidence_takes_the_quote_from_the_result_page(tmp_path):
    out = resolve_evidence(_marta(tmp_path), _f(2))
    assert out["step"] == 2 and out["evidence_step"] == 3
    assert QUOTE in out["observation_quote"]
    assert out["verified"] is True


def test_resolve_evidence_keeps_the_cited_page_when_the_quote_is_there(tmp_path):
    texts = ["a", "b", f"Pole {QUOTE}", f"Wynik: {QUOTE}", "d"]
    out = resolve_evidence(_marta(tmp_path, texts=texts), _f(2))
    assert "evidence_step" not in out and QUOTE in out["observation_quote"]


def test_resolve_evidence_ignores_a_page_two_steps_on(tmp_path):
    out = resolve_evidence(_marta(tmp_path, texts=["a", "b", "c", "d", f"Wynik: {QUOTE}"], n_actions=5), _f(2))
    assert "evidence_step" not in out
    assert QUOTE not in out["observation_quote"]


def test_resolve_evidence_clears_a_stale_evidence_step(tmp_path):
    out = resolve_evidence(_marta(tmp_path), _f(3, evidence_step=4))
    assert "evidence_step" not in out


# --- the quote check that releases a held finding ----------------------------

ONE_QUOTE = f"Wyszukiwarka wskazała '{QUOTE}' zamiast placówki w Krakowie."


def test_quotes_visible_at_step_accepts_the_result_page(tmp_path):
    store = _marta(tmp_path)
    for i, html in enumerate(["<p>x</p>"] * 3 + [f"<main>{QUOTE}</main>"]):
        store.write_simp_html(i, html)
    assert attribution.quotes_visible_at_step(store, _f(2, evidence=ONE_QUOTE)) == [QUOTE]
    assert attribution.quotes_visible_at_step(store, _f(1, evidence=ONE_QUOTE)) == []  # step 2's page is not 1's result


def test_quotes_visible_at_step_never_reads_two_steps_on(tmp_path):
    store = _marta(tmp_path, texts=["a", "b", "c", "d", "e"], n_actions=5)
    for i, html in enumerate(["<p>x</p>"] * 4 + [f"<main>{QUOTE}</main>"]):
        store.write_simp_html(i, html)
    assert attribution.quotes_visible_at_step(store, _f(2, evidence=ONE_QUOTE)) == []


# --- the hold that is about the typed address stays as it was -----------------

def test_guessed_address_hold_is_unchanged(tmp_path):
    guessed = {3: {"url": f"{SITE}/placowki", "action_step": 2, "from_error_page": False}}
    blames = _f(2, evidence="Link do placówek prowadzi do błędu 404, strona jest zepsuta.")
    with patch.object(attribution, "finding_blames_dead_address", return_value=True):
        assert attribution._guessed_address_cause(blames, guessed)["step"] == 3
        assert attribution._guessed_address_cause(_f(3), guessed)["step"] == 3
        assert attribution._guessed_address_cause(_f(1), guessed) is None
        assert attribution._guessed_address_cause(_f(4), guessed) is None


def test_only_an_action_step_has_a_following_observation(tmp_path):
    store = _marta(tmp_path)
    assert following_observation_step(store, -1) is None  # not a step: observation 0 is no action's result
    assert following_observation_step(store, "x") is None
    assert following_observation_step(store, 2) == 3
