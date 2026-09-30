"""A held-back finding says why, by the signal that held it.

In the 2026-09-30 hyundai run a remark about the test-drive page's copy ("the
form asks you to wait for a call") was held with "prawdopodobna awaria
narzędzia, nie strony". The quoted sentence is real page text. The only thing
the trace shows is that a click near that step was blocked — a different claim,
and the one the reason should have made."""
import json

import pytest

from simulated_web_agent.main import attribution
from simulated_web_agent.main.reporter import _gate_reason, _harness_reason
from simulated_web_agent.main.trace_store import TraceStore


def _trace(tmp_path, actions, observations):
    store = TraceStore(tmp_path / "persona")
    store.write_action_trace([json.dumps(a) for a in actions])
    for obs in observations:
        store.append_observation(obs)
    return store


def _ok():
    return {"tabs": [{"url": "https://x.pl/", "is_active": True}], "error": None}


def _failed(err):
    return {"tabs": [{"url": "https://x.pl/", "is_active": True}], "error": err}


BLOCKED = ("Error executing action: Locator.click: Timeout 5000ms exceeded. Call log: "
           "<a class=\"logo__link\"> from <div> subtree intercepts pointer events")


def test_the_cause_names_the_signature_and_the_error(tmp_path):
    store = _trace(tmp_path, [{"action": "click", "target": "a"}, {"action": "click", "target": "b"}],
                   [_ok(), _failed(BLOCKED)])
    causes = attribution.harness_suspect_causes(store)
    assert causes[0] == ("executor_error", "kliknięcie zablokowała inna warstwa strony")
    assert causes[1] == ("executor_error", "kliknięcie zablokowała inna warstwa strony")
    assert set(causes) == attribution.harness_suspect_steps(store)


@pytest.mark.parametrize("err, detail", [
    ("Error executing action: Locator.click: Timeout 10000ms exceeded", "element nie zareagował w limicie czasu"),
    ("Error executing action: Element 'x' not found on page", "element nie został znaleziony na stronie"),
    ("Error executing action: boom", "akcja nie została wykonana"),
])
def test_each_kind_of_executor_error_is_described(tmp_path, err, detail):
    store = _trace(tmp_path, [{"action": "click", "target": "a"}, {"action": "click", "target": "b"}],
                   [_ok(), _failed(err)])
    assert attribution.harness_suspect_causes(store)[0][1] == detail


def test_a_finding_carries_the_cause_of_its_cited_step(tmp_path):
    store = _trace(tmp_path, [{"action": "click", "target": "a"}, {"action": "click", "target": "b"}],
                   [_ok(), _failed(BLOCKED)])
    finding = {"heuristic_id": 7, "severity": "drobny", "step": 1,
               "evidence": "Formularz prosi o oczekiwanie na telefon.", "recommendation": "Kalendarz."}
    attribution.flag_harness_suspects(store, [finding])
    assert finding["harness_suspect"] is True
    assert finding["harness_cause"] == {"code": "executor_error",
                                        "detail": "kliknięcie zablokowała inna warstwa strony", "step": 1}


def test_a_finding_that_stands_has_no_cause(tmp_path):
    store = _trace(tmp_path, [{"action": "click", "target": "a"}], [_ok()])
    finding = {"heuristic_id": 7, "severity": "drobny", "step": 0, "evidence": "x", "recommendation": "y"}
    attribution.flag_harness_suspects(store, [finding])
    assert finding["harness_suspect"] is False and finding["harness_cause"] is None


def test_the_reason_says_what_the_trace_shows_and_what_it_cannot_settle():
    text = _harness_reason({"code": "executor_error", "step": 22,
                            "detail": "kliknięcie zablokowała inna warstwa strony"})
    assert "w kroku 22" in text and "kliknięcie zablokowała inna warstwa strony" in text
    assert "nie da się odróżnić" in text
    assert "awaria narzędzia" not in text, "it does not claim the tool failed"


@pytest.mark.parametrize("code, expected", [
    ("consent", "baner zgody"),
    ("bot_wall", "weryfikacji antybotowej"),
    ("perception_gap", "opis strony był niemal pusty"),
    ("fallback", "wycofał się po nieudanych próbach"),
    ("repeat", "powtórzono wielokrotnie"),
    ("claim_contradicted", "przeczy przebiegowi sesji"),
])
def test_every_cause_has_its_own_sentence(code, expected):
    assert expected in _harness_reason({"code": code, "step": 3, "detail": ""})


def test_an_old_finding_without_a_cause_still_gets_a_true_sentence():
    assert _gate_reason({"harness_suspect": True}) == "w tym kroku lub tuż przed nim wystąpiła usterka narzędzia"


def test_the_gate_reason_uses_the_cause():
    f = {"harness_suspect": True,
         "harness_cause": {"code": "consent", "detail": "", "step": 4}}
    assert "baner zgody" in _gate_reason(f)
