"""Elements that took the agent's clicks, in more than one session.

Playwright refuses a click when another element sits on the spot and says which:
"<span class="s-color-preview"> intercepts pointer events". One such error is a
click the tool missed, and attribution holds the findings near it for a human.
The same element taking the clicks of several participants is a different
statement: the obstacle is steady, not a one-off, and a visitor in a window of the
same size could land on it too. That is a fact about the trace, countable without
asking a model, and it is the strongest thing the trace can say about a held claim
that "the dialog blocked me".

Recorded runs name a colour-swatch preview and a hidden carousel button this way.
"""
from __future__ import annotations

import re
from collections import Counter

from .textutil import view_key

# Fewer than this many sessions is a single participant's bad luck.
MIN_SESSIONS = 2

# "- <span class="x"></span> intercepts pointer events", and, when the element
# that took the click sits inside another subtree,
# "- <button class="y">…</button> from <div class="z">…</div> subtree intercepts pointer events".
# The first element named is the one that took the click.
_INTERCEPTOR = re.compile(
    r"-\s*<(?P<tag>[a-z][a-z0-9-]*)(?P<attrs>[^>]*)>.{0,600}?intercepts pointer events", re.S)
_CLASS = re.compile(r'\bclass\s*=\s*"([^"]*)"')
_ID = re.compile(r'\bid\s*=\s*"([^"]+)"')
_LABEL = re.compile(r'\baria-label\s*=\s*"([^"]+)"')


def intercepting_element(error: str) -> str | None:
    """A short selector for the element that took the click, or None.

    None for a bare tag: "div" says nothing about WHICH div, and counting every
    anonymous div as one obstacle would manufacture agreement between sessions.
    """
    if not isinstance(error, str) or "intercepts pointer events" not in error:
        return None
    matches = list(_INTERCEPTOR.finditer(error))
    if not matches:
        return None
    m = matches[-1]  # the last retry is the settled description
    tag, attrs = m.group("tag"), m.group("attrs") or ""
    cls = _CLASS.search(attrs)
    # data-v-… are the framework's scoped-style hashes, not the element's name.
    classes = [c for c in (cls.group(1).split() if cls else []) if not c.startswith("data-v")]
    if classes:
        return f"{tag}." + ".".join(classes[:2])
    ident = _ID.search(attrs)
    if ident:
        return f"{tag}#{ident.group(1)}"
    label = _LABEL.search(attrs)
    if label:
        return f'{tag}[aria-label="{label.group(1)}"]'
    return None


def _active_url(observation: dict) -> str:
    tabs = (observation or {}).get("tabs") or []
    for want_active in (True, False):
        for tab in tabs:
            url = str((tab or {}).get("url") or "")
            if url.startswith("http") and (not want_active or tab.get("is_active")):
                return url
    return ""


def repeated_obstacles(stores: list, participants: int) -> list[dict]:
    """The elements that took clicks in at least MIN_SESSIONS sessions, most first.

    [{"element": "span.s-color-preview", "sessions": 3, "of": 6, "pages": [...]}]
    """
    found: dict[str, dict] = {}
    for store in stores or []:
        name = str(getattr(store, "trace_dir", "")).replace("\\", "/").rstrip("/").split("/")[-1]
        try:
            observations = store.read_observations()
        except Exception:
            continue
        for obs in observations:
            error = (obs or {}).get("error") if isinstance(obs, dict) else None
            element = intercepting_element(error) if isinstance(error, str) else None
            if not element:
                continue
            entry = found.setdefault(element, {"sessions": set(), "pages": Counter()})
            entry["sessions"].add(name)
            page = view_key(_active_url(obs))
            if page:
                entry["pages"][page] += 1
    rows = [{"element": element, "sessions": len(e["sessions"]), "of": int(participants or 0),
             "pages": [p for p, _ in e["pages"].most_common(3)]}
            for element, e in found.items() if len(e["sessions"]) >= MIN_SESSIONS]
    rows.sort(key=lambda r: (-r["sessions"], r["element"]))
    return rows


OBSTACLES_INTRO = (
    "W tych sesjach narzędzie próbowało kliknąć kontrolkę, ale w tym miejscu strony był inny "
    "element, który przejął kliknięcie. Gdy ten sam element robi to u kilku uczestników, "
    "przeszkoda jest stała, a nie jednorazowa, i użytkownik w oknie tej samej wielkości mógłby "
    "trafić w ten sam element. To nie dowodzi, że klient straci przez to konwersję — sprawdź w "
    "zwykłej przeglądarce, czy kontrolka za tym elementem da się kliknąć.")


def obstacle_text(row: dict) -> str:
    """One obstacle as a sentence for a report."""
    where = ", ".join(row.get("pages") or [])
    return (f"Element „{row['element']}” przejmował kliknięcia w {row['sessions']} z "
            f"{row['of']} sesji" + (f" (strona: {where})" if where else "") + ".")
