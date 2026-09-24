import os

# Use litellm's bundled model map, never the one it downloads at import.
#
# By default litellm fetches model_prices_and_context_window.json from GitHub
# every time it is imported, and resolves each router entry's provider against
# whatever that file says today. When the upstream file dropped
# claude-sonnet-4-20250514, building the router raised "LLM Provider NOT
# provided" — at import, for a provider this app does not even use — so the
# backend could not start at all. A running instance survives only until its
# next restart. Startup must not depend on the current contents of a file on
# someone else's GitHub; the bundled map is versioned with the pinned litellm.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from . import agent, executor  # noqa: E402
