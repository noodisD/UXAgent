"""The persona perceives the page's own words, in the page's own language.

perceive.txt told the model to "translate the HTML content into natural
language", and it did: in 112 of 414 archived steps (18 of 24 sessions) the
observation the persona acted on was not in the page's language — 111 English
paraphrases and, in the 2026-09-25 hyundai replay, a Spanish rendering of the
Polish menu. That one became a "verified" serious H2 finding, "Serwis wyświetla
treści w języku hiszpańskim", about text the site never showed; the page's own
HTML at that step had none of the Spanish words.

Replayed offline on 40 recorded steps × 3 samples with flash-lite: the page's
language 116/120 with this prompt against 59/120 before, and recall of the
page's words 0.97-0.98 against 0.86-0.91.
"""
from simulated_web_agent.agent.prompt_registry import registry as prompts


def test_the_prompt_asks_for_a_transcription_not_a_translation():
    p = prompts.get("perceive")
    assert "translate the HTML content" not in p
    assert "Keep the page's own language" in p
    assert "Never translate the page into another language" in p


def test_the_output_contract_is_unchanged():
    """agent.perceive parses {"observations": [one string]}."""
    p = prompts.get("perceive")
    assert '{"observations": [' in p
