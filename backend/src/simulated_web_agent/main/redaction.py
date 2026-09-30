"""Keep the harness's own vocabulary out of the client's report.

The persona sees the executor's error channel — it has to, or it could not
re-plan after a failed click. What it must not do is quote it. Re-running the
first external review's hyundai.com.pl case produced this survey answer, which
would have gone to a client verbatim:

    "…co skutkowało wielokrotnymi błędami 'last_action_error' podczas klikania
    'Następny krok'."

`last_action_error` is an internal variable name in this tool. A reader who
notices it learns two things at once: that the difficulty was ours, and that
nobody checked the document. Findings are gated by attribution.py, but survey
answers and objective reflections are free text and bypass every gate.

Measured over the 35 archived sessions: 4 of 1560 free-text strings carried an
internal identifier. Rare, and worth removing precisely because it is rare —
this is the kind of detail a sceptical reviewer finds first.

Redaction here is deliberately narrow. It removes identifiers and machine error
strings, not the substance of the complaint: if the button genuinely did
nothing, the sentence still says so, and attribution decides separately whether
that counts as a finding. Deleting the complaint instead would be suppression,
which is the opposite failure.
"""
import re

# Internal names and machine error strings. Ordered longest-first within each
# pattern so the more specific phrasing wins.
_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Our own field names, quoted or bare.
    (re.compile(r"['\"`]?\blast_action_error\b['\"`]?", re.I), "błąd interakcji"),
    (re.compile(r"['\"`]?\bactionElementNotFound\b['\"`]?", re.I), "błąd interakcji"),
    # Executor error prefixes (env.py) — see attribution._EXECUTOR_ERROR_MARKERS.
    (re.compile(r"\bError executing action:\s*", re.I), ""),
    (re.compile(r"\bInvalid JSON action format:\s*", re.I), ""),
    (re.compile(r"\bMissing required parameter in action:\s*", re.I), ""),
    # Playwright internals.
    (re.compile(r"\bLocator\.\w+:\s*", re.I), ""),
    (re.compile(r"\bTimeout \d+ms exceeded\b", re.I), "przekroczono czas oczekiwania"),
    (re.compile(r"\bnet::[A-Z_]+\b"), "błąd połączenia"),
    # Parser vocabulary that only exists inside this tool.
    (re.compile(r"\b(parser[- ]?)?semantic[- ]?id\b", re.I), "element"),
)

# Marks text that describes the automation failing rather than the site. Used
# to tell the aggregate pass that a complaint is self-reported and unconfirmed,
# not to delete it.
_HARNESS_SIGNALS = (
    "last_action_error",
    "error executing action",
    "invalid json action",
    "missing required parameter",
    "locator.",
    "net::err",
    "semantic id",
    # "element" is this tool's vocabulary, not a person's. A real complaint is
    # "I couldn't click it" / "nie dało się w to kliknąć"; "not a clickable
    # element" is the parser describing its own model of the page.
    "clickable element",
    "klikalnym elementem",
    "klikalnego elementu",
)


def redact_internals(text: str) -> str:
    """Replace this tool's internal vocabulary with plain language."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    # Collapse whitespace the substitutions may have left behind, without
    # touching newlines that carry structure.
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"\s+([,.;:])", r"\1", out).strip()


# How a person says an interaction would not work. On its own this is ordinary —
# people do report broken buttons — so it counts only beside a trace showing the
# tool itself failed in that session (see reporter). The 2026-09-30 hyundai run:
# "divy blokujące klikanie", "zapętlający się konfigurator", "błędy skryptów" and
# a recommended "naprawa struktury warstw (z-index)" were echoes of Playwright's
# "<div> intercepts pointer events" and a retried click, and reached the client
# as site facts because no word on the harness list appeared in them.
#
# Whole words: "nie działa" also sits inside "Usprawnie|nie działa|nia filtrów" and
# "jedy|nie działa|jące strzałki", and a bare "uniemożliwia" opens "Brak informacji
# o racie uniemożliwia porównanie ofert" — all ordinary remarks about content.
_INTERACTION_TROUBLE = re.compile(
    r"blokując|zablokow|uniemożliw\w*\s+(?:\w+\s+){0,2}(?:klik|przycisk|przejśc|przejść|nawigac)|"
    r"(?<!\w)nie\s+działa|(?<!\w)niedziała|(?<!\w)nie\s+reagow|zapętl|zacina|zaciął|"
    r"błęd\w*\s+skrypt|błęd\w*\s+warstw|z-index|\bdiv|warstw\w*\s+(?:interfejs|strony)|"
    r"nakładając\w*\s+się|nie\s+dało\s+się\s+klikn",
    re.I)


def describes_interaction_trouble(text: str) -> bool:
    """True when free text says an interaction did not work (clicks blocked,
    controls dead, a loop). Only evidence of something, never proof: pair it
    with the session's own record of tool failures."""
    return bool(isinstance(text, str) and text and _INTERACTION_TROUBLE.search(text))


def mentions_harness_fault(text: str) -> bool:
    """True when free text describes this tool's own interaction failing."""
    if not isinstance(text, str) or not text:
        return False
    low = text.lower()
    return any(s in low for s in _HARNESS_SIGNALS)


def clean(node):
    """Redact every string in a nested structure, leaving its shape intact."""
    if isinstance(node, str):
        return redact_internals(node)
    if isinstance(node, list):
        return [clean(v) for v in node]
    if isinstance(node, dict):
        return {k: clean(v) for k, v in node.items()}
    return node
