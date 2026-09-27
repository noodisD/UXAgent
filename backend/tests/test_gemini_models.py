"""The Gemini tiers point at models that exist, where they exist.

The 2.5 family is shut down on 2026-10-16, and its 3.x successors answer 404
outside Vertex's global endpoint (measured 2026-09-27: us-central1 404,
global 200). A regional VERTEXAI_LOCATION alone would fail every call.
"""
from simulated_web_agent.agent.gpt import chat_router, slow_chat_router


def _gemini_entries():
    for router in (chat_router, slow_chat_router):
        for entry in router.model_list:
            if entry["model_name"].startswith("gemini"):
                yield entry["model_name"], entry["litellm_params"]


def test_every_gemini_tier_is_on_a_model_that_is_not_retiring():
    entries = dict(_gemini_entries())
    assert set(entries) == {"gemini", "gemini_lite", "gemini_report"}
    for name, params in entries.items():
        assert "gemini-2." not in params["model"], (name, params["model"])


def test_every_gemini_tier_uses_the_global_endpoint():
    for name, params in _gemini_entries():
        assert params.get("vertex_location") == "global", name


def test_the_tiers_keep_their_roles():
    entries = dict(_gemini_entries())
    assert "flash-lite" in entries["gemini_lite"]["model"]
    assert "-flash" in entries["gemini"]["model"] and "lite" not in entries["gemini"]["model"]
    assert "-pro" in entries["gemini_report"]["model"]
