"""
Evidence resolution for audit findings.

Every client-facing claim must be traceable to real proof: the step screenshot,
the exact page URL, and the agent's verbatim observation. The LLM only *cites*
a step; this module verifies the citation against the trace (the source of
truth) and stamps each finding `verified: True/False`. Findings that cannot be
grounded are kept but must be rendered with an "unverified" label.
"""
import html as _html
import logging
import re

from .trace_store import TraceStore

log = logging.getLogger(__name__)

_QUOTE_CHARS = 400
_PAGE_QUOTE_CHARS = 240

# What a finding quotes from the page — 'null kW (null KM)', 'EXECUTIVE MY27'.
_QUOTED = re.compile(
    r"['\"‘’‚“”„]([^'\"‘’‚“”„]{3,80})"
    r"['\"‘’‚“”„]")


def _visible_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup or "")
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _find(text: str, needle: str) -> int:
    """Index of `needle` in `text`, case-insensitive; -1 when absent.

    A short single word ('info', 'OK') matches only as a whole word. As a substring
    it found "informacyjny" in a page's legal footer, and the report showed the
    footer as the proof for a finding about an 'info' button."""
    needle = needle.strip()
    if len(needle) <= 6 and " " not in needle:
        m = re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text, re.IGNORECASE)
        return m.start() if m else -1
    return text.lower().find(needle.lower())


def _window(text: str, needle: str, width: int) -> str:
    i = _find(text, needle)
    if i < 0:
        return ""
    start = max(0, i - (width - len(needle)) // 2)
    end = min(len(text), start + width)
    out = text[start:end].strip()
    return ("…" if start > 0 else "") + out + ("…" if end < len(text) else "")


# Broken rendering: text a page shows only when its template or data failed.
_ARTIFACT_RE = re.compile(
    r"(\bnull\b|\bundefined\b|\bNaN\b|\[object \w+\]|\{\{|\}\}|lorem ipsum)", re.IGNORECASE)


def rendering_artifact(evidence: str) -> str:
    """The quoted fragment of broken rendering a finding names, or "".

    Only fragments the finding quotes count — 'null kW (null KM)' — so a finding
    that merely uses the word "null" in its prose does not qualify.
    """
    for fragment in sorted(set(_QUOTED.findall(evidence or "")), key=len, reverse=True):
        if _ARTIFACT_RE.search(fragment):
            return fragment.strip()
    return ""


def pages_contain(store: TraceStore, fragment: str) -> bool:
    """Did any page this session recorded show `fragment`?"""
    if not fragment or store is None:
        return False
    for path in sorted((store.trace_dir / "simp_html").glob("simp_html_*.html")):
        try:
            if _find(_visible_text(path.read_text(encoding="utf-8", errors="replace")), fragment) >= 0:
                return True
        except OSError:
            continue
    return False


def page_quote(store: TraceStore, step, evidence: str) -> str:
    """The page's own words at the cited step, centred on what the finding quotes.

    The report used to print the first 400 characters of the perception model's
    description of the page — in English, always the page header ("Close button.
    Navigation links: … Page title: Wybie"), however unrelated. For the
    "null kW (null KM)" finding that meant a quote cut off before the word null.
    The simplified page holds the real text; this returns the part that proves
    the claim, or "" when the finding quotes nothing that is on the page.
    """
    try:
        step = int(step)
    except (TypeError, ValueError):
        return ""
    path = store.trace_dir / "simp_html" / f"simp_html_{step}.html"
    try:
        text = _visible_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    for fragment in sorted(set(_QUOTED.findall(evidence or "")), key=len, reverse=True):
        found = _window(text, fragment.strip(), _PAGE_QUOTE_CHARS)
        if found:
            return found
    return ""


def following_observation_step(store: TraceStore, step) -> int | None:
    """The observation holding the RESULT of the action at `step`, or None.

    observations[k + 1] is what action k loaded (observation 0 is the landing page), so
    a finding that cites the step of a typed action has its evidence one observation on.
    None unless `step` is an action step and that page was read by the participant: the
    observation a session cut short captured and nobody perceived has no text, and is
    never evidence. Only the next step counts, never a later one."""
    try:
        step = int(step)
        n_actions = len(store.read_action_trace() or [])
        n_observations = len(store.read_observations() or [])
    except Exception:
        return None
    nxt = step + 1
    if not 0 <= step < n_actions or nxt >= n_observations:
        return None
    return nxt if store.read_observation_step_text(nxt).strip() else None


def resolve_evidence(store: TraceStore, finding: dict) -> dict:
    """Enrich a finding with verified evidence from the trace.

    Adds/overwrites: step (int|None), url, screenshot (relative path or None),
    observation_quote, verified. When the trace URL disagrees with the
    LLM-claimed one, the trace wins and the claim is kept as `claimed_url`.
    When what the finding quotes is on the page the cited action loaded rather
    than on the page it was typed on, that page is the evidence and `evidence_step`
    names it; `step` stays as cited.
    """
    enriched = dict(finding)
    enriched.pop("evidence_step", None)

    step = enriched.get("step")
    try:
        step = int(step)
    except (TypeError, ValueError):
        step = None

    n_steps = len(store.read_observations())
    if step is None or not 0 <= step < n_steps:
        enriched.update(
            {"step": step, "screenshot": None, "observation_quote": "", "verified": False}
        )
        return enriched

    trace_url = store.url_at_step(step)
    claimed_url = enriched.get("url") or ""
    if trace_url:
        if claimed_url and claimed_url != trace_url:
            enriched["claimed_url"] = claimed_url
        enriched["url"] = trace_url

    screenshot = None
    for full_page in (False, True):
        path = store.screenshot_path(step, full_page=full_page)
        if path.exists():
            screenshot = str(path.relative_to(store.trace_dir)).replace("\\", "/")
            break

    evidence_text = str(enriched.get("evidence") or "")
    observation = store.read_observation_step_text(step).strip()
    # Centred on what the finding quotes when possible, not the first 400
    # characters, which are the page header whatever the claim is about.
    fragments = sorted(set(_QUOTED.findall(evidence_text)), key=len, reverse=True)
    quote = ""
    for fragment in fragments:
        quote = _window(observation, fragment.strip(), _QUOTE_CHARS)
        if quote:
            break
    shown = page_quote(store, step, evidence_text)
    nxt = following_observation_step(store, step)
    if nxt is not None and not (quote or shown):
        # Nothing the finding quotes is on the cited page; the page its action loaded may have it.
        after = store.read_observation_step_text(nxt).strip()
        for fragment in fragments:
            quote = _window(after, fragment.strip(), _QUOTE_CHARS)
            if quote:
                break
        shown = page_quote(store, nxt, evidence_text)
        if quote or shown:
            enriched["evidence_step"] = nxt
            observation = after
    if not quote:
        quote = observation[:_QUOTE_CHARS] + ("…" if len(observation) > _QUOTE_CHARS else "")

    enriched.update(
        {
            "step": step,
            "screenshot": screenshot,
            "observation_quote": quote,
            "page_quote": shown,
            "verified": bool(screenshot or quote),
        }
    )
    return enriched


def resolve_all(store: TraceStore, findings: list) -> list:
    """resolve_evidence over a list, tolerating malformed entries."""
    out = []
    for f in findings or []:
        if isinstance(f, dict):
            try:
                out.append(resolve_evidence(store, f))
            except Exception:
                log.exception("evidence resolution failed for finding: %s", f)
                out.append({**f, "verified": False})
        else:
            out.append(f)
    return out
