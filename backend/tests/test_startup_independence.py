"""The backend must be able to start without asking GitHub what models exist.

litellm downloads model_prices_and_context_window.json from GitHub on import
and resolves every router entry's provider against it. When that upstream file
dropped claude-sonnet-4-20250514, building the router raised "LLM Provider NOT
provided" at import time — for the Anthropic entry, a provider this app does not
even use — and the backend could not start at all. Nothing in this repository
had changed. A running Cloud Run instance survives only until its next restart.

Two independent guards, each tested on its own so neither can quietly become
the only one: the bundled map is pinned, and every router entry names its
provider so none depends on inference from the map in the first place.
"""
import os


def test_importing_the_package_pins_the_bundled_model_map():
    import simulated_web_agent  # noqa: F401

    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP", "").lower() == "true"


def test_every_router_entry_names_its_provider():
    """A bare model id makes litellm guess the provider from the cost map, which
    is exactly the lookup that failed. The OpenAI, Gemini and Bedrock entries
    already carried a prefix; only Anthropic relied on inference."""
    from simulated_web_agent.agent import gpt

    bare = [
        entry["litellm_params"]["model"]
        for entry in gpt.chat_router.model_list
        if "/" not in str(entry["litellm_params"].get("model", ""))
    ]
    assert bare == [], f"router entries without an explicit provider: {bare}"


def test_the_container_pins_the_map_too():
    """Belt and braces: an import path that reaches litellm before the package
    initialiser would otherwise bypass the guard."""
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")
    assert "LITELLM_LOCAL_MODEL_COST_MAP=True" in dockerfile
