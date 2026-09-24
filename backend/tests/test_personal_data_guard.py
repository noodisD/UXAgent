"""The agent must never type an identity it does not have.

Nothing used to stop it. The only rule was a line in planning.txt, framed around
login walls and missing from the prompt that emits typing. Hyundai's test-drive
form asks for name, surname, email, phone and an SMS verification code — a
filled phone field can text whoever owns that number, and a submission books a
real dealer appointment. The first external reviewer's own T-Mobile task ended
"jeśli tak, to ją zakup".

Verified against the live Hyundai form: firstName, lastName, email,
phone-number and sms-code are all refused; the bot-trap "fax" field carries no
personal data and is left alone.
"""
import inspect

import pytest

from simulated_web_agent.executor import env as env_mod
from simulated_web_agent.executor.env import (
    INPUT_REFUSAL_PREFIX,
    SCOPE_REFUSAL_PREFIX,
    input_refusal_message,
    personal_field_reason,
    personal_text_reason,
)


# --- the fields -------------------------------------------------------------

@pytest.mark.parametrize("attrs", [
    {"name": "firstName", "type": "text"},
    {"name": "lastName"},
    {"type": "email", "name": "email"},
    {"type": "tel", "name": "phone-number"},
    {"name": "sms-code"},
    {"type": "password"},
    {"autocomplete": "cc-number"},
    {"autocomplete": "given-name"},
    {"placeholder": "Imię"},
    {"label": "Nazwisko"},
    {"aria_label": "Numer telefonu"},
    {"name": "pesel"},
])
def test_personal_fields_are_refused(attrs):
    assert personal_field_reason(attrs)


@pytest.mark.parametrize("attrs", [
    {"type": "search", "name": "q", "placeholder": "Szukaj"},
    {"name": "street", "placeholder": "Ulica i numer"},
    {"name": "postcode", "autocomplete": "postal-code"},
    {"placeholder": "Wpisz miasto"},
    {"name": "fax"},
    {},
])
def test_ordinary_fields_are_left_alone(attrs):
    """Address lookups are the whole point of an availability check — the
    reviewer's own task needed one — so over-blocking here would stop a legitimate
    audit path, not just an unsafe one."""
    assert personal_field_reason(attrs) is None


# --- the text itself ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    "jan.kowalski@gmail.com", "123 456 789", "+48 123-456-789", "123456789",
])
def test_personal_text_is_refused_whatever_the_field(text):
    """Catches the mislabelled field the attribute check cannot see."""
    assert personal_text_reason(text)


@pytest.mark.parametrize("text", [
    "ulica Zabawa 28, 30-653 Kraków", "150 000", "KONA Hybrid", "2026",
    "krem nawilżający", "Warszawa",
])
def test_ordinary_text_passes(text):
    assert personal_text_reason(text) is None


# --- how the refusal reaches the agent -------------------------------------

def test_the_refusal_is_a_policy_not_an_interaction_failure():
    """attribution.py reads "Error executing action:" as proof the step failed on
    our side and suppresses its findings. A refusal worked as intended."""
    msg = input_refusal_message("type=email")
    assert msg.startswith(INPUT_REFUSAL_PREFIX)
    assert not msg.startswith("Error executing action")
    assert INPUT_REFUSAL_PREFIX != SCOPE_REFUSAL_PREFIX


def test_the_refusal_tells_the_agent_what_to_do_next():
    msg = input_refusal_message("type=tel").lower()
    assert "end of this path" in msg and "note what the form asks for" in msg


def test_attribution_does_not_treat_the_refusal_as_our_fault():
    from simulated_web_agent.main.attribution import _EXECUTOR_ERROR_MARKERS

    msg = input_refusal_message("type=email").lower()
    assert not any(marker in msg for marker in _EXECUTOR_ERROR_MARKERS)


def test_both_typing_actions_are_guarded():
    """raw_type types into whatever has focus, so guarding only `type` would
    leave a way round the check."""
    src = inspect.getsource(env_mod.WebAgentEnv.step)
    type_at = src.index('action_name == "type"')
    raw_at = src.index('action_name == "raw_type"')
    guard_calls = [i for i in range(len(src)) if src.startswith("_personal_data_refusal", i)]
    assert any(type_at < g < raw_at for g in guard_calls), "type is unguarded"
    assert any(g > raw_at for g in guard_calls), "raw_type is unguarded"
