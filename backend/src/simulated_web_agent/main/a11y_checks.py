"""Deterministic accessibility checks over the captured aria snapshot.

METHODOLOGY_PLAN §3.4. "Does this image have alt text?" is decidable, so code
decides it. The LLM's job shrinks to what it is actually good at: narrating and
scoring the issues code found.

Why this exists
───────────────
The accessibility pass used to paste every step's raw aria snapshot into the
prompt. One pepco.pl homepage snapshot is 377,000 characters; a 30-step session
built a 1.66M-char (~416k token) prompt. It sat near the context limit, so it
failed probabilistically — slow when it worked, and in run 2026-07-16_13-41-14 it
left one persona with `overall_score: None` and zero findings while the other
scraped through. Retrying could not fix a payload that large.

The snapshot is also mostly repetition: a product carousel yields the same
unlabelled image forty times. Forty occurrences of one defect are one defect
with a count, which is both smaller and more honest than forty findings.

Scope honestly: these are **mobile-web heuristics** derived from the aria tree,
not a WCAG conformance audit. Contrast and tap-target size need the live DOM and
belong to viewport_scan.py.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .trace_store import TraceStore

log = logging.getLogger(__name__)

# The doc's mobile criterion: body text below this is a readability barrier.
_MIN_FONT_PX = 12

# Roles that must carry an accessible name to be usable by a screen reader.
_NAMED_CONTROLS = frozenset({
    "button", "link", "textbox", "searchbox", "checkbox", "radio",
    "combobox", "switch", "slider", "menuitem", "tab", "option",
})

# At most this many distinct examples per check reach the prompt. Beyond a
# handful the model learns nothing new, and the payload must not scale with the
# page — that is the bug this module exists to fix.
_MAX_EXAMPLES_PER_CHECK = 5

# "- role", '- role "name":', "- 'role \"name\"':" — Playwright single-quotes the
# whole node when the name contains punctuation.
_NODE = re.compile(
    r"""^\s*-\s+                 # list marker
        '?                       # optional opening quote around the whole node
        (?P<role>[a-zA-Z][\w-]*) # role
        (?:\s+"(?P<name>[^"]*)")?  # optional quoted accessible name
        '?                       # optional closing quote
        \s*:?\s*$                # optional trailing colon
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class AriaNode:
    role: str
    name: str


def parse_aria_snapshot(text: str) -> list[AriaNode]:
    """Flatten Playwright's aria-snapshot text into (role, name) nodes.

    Property lines (`- /url: ...`) and text lines (`- text: ...`) are not nodes
    and are skipped. Nesting is irrelevant to every check here, so depth is
    dropped rather than modelled — keeping this a parser, not a tree builder.
    """
    nodes: list[AriaNode] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        # `- text: foo` / `- /url: bar` are properties of the node above.
        body = stripped[1:].strip()
        if body.startswith("/") or re.match(r"^(text|url)\s*:", body):
            continue
        m = _NODE.match(line)
        if m:
            nodes.append(AriaNode(m.group("role"), m.group("name") or ""))
    return nodes


def _steps(raw) -> list[tuple[int, object]]:
    """Normalise the trace's per-step mapping to sorted (step, value) pairs."""
    if isinstance(raw, dict):
        out = []
        for k, v in raw.items():
            try:
                out.append((int(k), v))
            except (TypeError, ValueError):
                continue
        return sorted(out)
    if isinstance(raw, list):
        return list(enumerate(raw))
    return []


def extract_a11y_candidates(store: TraceStore) -> dict:
    """Find accessibility issues in the trace, deduplicated and step-cited.

    Returns {"candidates": [...], "steps_scanned": int}. Each candidate carries
    `check`, `step` (first step it was seen on, for evidence resolution),
    `occurrences`, and a small `examples` list. Size is bounded by the number of
    checks, not by the size of the page.
    """
    snapshots = _steps(store.read_a11y_snapshots())
    fonts = dict(_steps(store.read_font_sizes()))

    # key -> aggregate. Keyed so the same defect on many nodes/steps collapses.
    agg: dict[tuple, dict] = {}

    def _add(key: tuple, step: int, check: str, example: str = "", **extra):
        entry = agg.get(key)
        if entry is None:
            agg[key] = entry = {
                "check": check, "step": step, "_per_step": {},
                "examples": [], **extra,
            }
        entry["_per_step"][step] = entry["_per_step"].get(step, 0) + 1
        if example and len(entry["examples"]) < _MAX_EXAMPLES_PER_CHECK \
                and example not in entry["examples"]:
            entry["examples"].append(example)

    for step, text in snapshots:
        if not isinstance(text, str):
            continue
        nodes = parse_aria_snapshot(text)
        if not nodes:
            continue

        for n in nodes:
            named = bool(n.name.strip())
            if n.role == "img" and not named:
                _add(("img_alt",), step, "image_missing_alt")
            elif n.role in _NAMED_CONTROLS and not named:
                _add(("ctl", n.role), step, "control_missing_name", role=n.role)

        # A page with no main landmark forces screen-reader users to wade
        # through the header on every navigation.
        if not any(n.role == "main" for n in nodes):
            _add(("no_main",), step, "missing_main_landmark")

    for step, sizes in fonts.items():
        nums = [s for s in (sizes or []) if isinstance(s, (int, float))]
        below = [s for s in nums if s < _MIN_FONT_PX]
        if below:
            entry = agg.get(("small_text",))
            _add(("small_text",), step, "text_below_12px", min_size_px=min(below))
            entry = agg[("small_text",)]
            entry["min_size_px"] = min(entry["min_size_px"], min(below))

    # Report counts the reader can trust. `occurrences` sums matches across every
    # snapshot, and a revisited page is snapshotted again — so 1760 matches is
    # NOT 1760 distinct images. Give the per-page figure and the spread instead,
    # or the narrator will state the total as a defect count.
    candidates = []
    for c in agg.values():
        per_step = c.pop("_per_step")
        worst_step, worst_count = max(per_step.items(), key=lambda kv: (kv[1], -kv[0]))
        c["occurrences"] = sum(per_step.values())
        c["steps_affected"] = len(per_step)
        c["max_on_one_page"] = worst_count
        # Cite the worst page, not the first sighting: `step` and
        # `max_on_one_page` end up in the same sentence, so they must be true of
        # the same page. It is also the strongest evidence and the most useful
        # screenshot. evidence.py resolves the citation from here.
        c["step"] = worst_step
        if not c["examples"]:
            c.pop("examples")
        candidates.append(c)
    candidates.sort(key=lambda c: (-c["occurrences"], c["step"]))

    log.info("[a11y_checks] %d candidate(s) from %d snapshot(s) in %s",
             len(candidates), len(snapshots), store.trace_dir)
    return {"candidates": candidates, "steps_scanned": len(snapshots)}
