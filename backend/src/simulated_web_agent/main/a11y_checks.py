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
        (?:\s*\[[^\]]*\])*       # attributes: [level=2] [selected] [disabled]
        '?                       # closing quote when it follows the attributes
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


# Controls whose accessible name already says what an icon inside them means.
_NAMING_CONTAINERS = frozenset({
    "link", "button", "menuitem", "tab", "option", "checkbox", "radio", "switch",
})


def classify_unnamed_images(text: str) -> dict:
    """Where the unnamed images of one snapshot sit.

      in_named_control — an icon inside a button or link that has a name: the
                         control says what it is, nothing is lost to a screen
                         reader ("Studio", "Full screen", "Dowiedz się więcej")
      outside_content  — at the top level of the tree, outside every region of
                         the page: tracking pixels and similar
      content          — the rest: images whose meaning is really missing

    The narrator read "90 z 517 obrazów bez opisu (17%)" as blind users losing
    the car models; in the 2026-09-26 run 65 of the 90 were such icons, 23
    were top-level pixels, 2 were in the footer and none was a model photo —
    the models were named.
    """
    out = {"in_named_control": 0, "outside_content": 0, "content": 0}
    stack: list[tuple[int, str, str]] = []
    for line in (text or "").splitlines():
        m = _NODE.match(line)
        if not m or not line.strip().startswith("-"):
            continue
        body = line.strip()[1:].strip()
        if body.startswith("/") or re.match(r"^(text|url)\s*:", body):
            continue
        depth = (len(line) - len(line.lstrip(" "))) // 2
        while stack and stack[-1][0] >= depth:
            stack.pop()
        role, name = m.group("role"), (m.group("name") or "").strip()
        if role == "img" and not name:
            if any(r in _NAMING_CONTAINERS and n for _d, r, n in stack):
                out["in_named_control"] += 1
            elif depth == 0:
                out["outside_content"] += 1
            else:
                out["content"] += 1
        stack.append((depth, role, name))
    return out


def describe_unnamed_controls(text: str) -> list[tuple[str, str]]:
    """(role, context) for each unnamed control: the text the page shows just
    before it and, for a list control, the option already selected.

    "combobox without a name" was written up as blocking the test-drive form
    completely, when the page printed "Model" above it and it already held
    "IONIQ 5". Both are in the snapshot; handing them over lets the finding
    say what a screen reader actually announces.
    """
    out: list[tuple[str, str]] = []
    lines = (text or "").splitlines()

    def depth(line: str) -> int:
        return (len(line) - len(line.lstrip(" "))) // 2

    for i, line in enumerate(lines):
        m = _NODE.match(line)
        if not m or m.group("role") not in _NAMED_CONTROLS or (m.group("name") or "").strip():
            continue
        d, parts = depth(line), []
        for prev in reversed(lines[:i]):
            if depth(prev) < d:
                break
            body = prev.strip()[1:].strip() if prev.strip().startswith("-") else ""
            if depth(prev) == d and body.startswith("text:"):
                parts.append(f'visible text before it: "{body[5:].strip()[:60]}"')
                break
            if depth(prev) == d and body:
                break
        for nxt in lines[i + 1:]:
            if depth(nxt) <= d:
                break
            opt = _NODE.match(nxt)
            if opt and opt.group("role") == "option" and "[selected" in nxt:
                parts.append(f'selected option: "{(opt.group("name") or "").strip()[:60]}"')
                break
        if parts:
            out.append((m.group("role"), "; ".join(parts)))
    return out


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

    image_places: dict[int, dict] = {}
    for step, text in snapshots:
        if not isinstance(text, str):
            continue
        nodes = parse_aria_snapshot(text)
        if not nodes:
            continue
        image_places[step] = classify_unnamed_images(text)

        for n in nodes:
            named = bool(n.name.strip())
            if n.role == "img" and not named:
                _add(("img_alt",), step, "image_missing_alt")
            elif n.role in _NAMED_CONTROLS and not named:
                _add(("ctl", n.role), step, "control_missing_name", role=n.role)
        for role, context in describe_unnamed_controls(text):
            entry = agg.get(("ctl", role))
            if entry is not None and len(entry["examples"]) < _MAX_EXAMPLES_PER_CHECK \
                    and context not in entry["examples"]:
                entry["examples"].append(context)

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
    # Distinct pages as well as steps: the narrator wrote "problem na 1 z 18
    # przeskanowanych stron" where 18 were snapshots of a handful of pages.
    def _page(step):
        try:
            # Full URL, as measure_names counts pages: each configurator step
            # (#/trims, #/colours) is its own view.
            return store.url_at_step(step) or f"step {step}"
        except Exception:
            return f"step {step}"

    candidates = []
    for c in agg.values():
        per_step = c.pop("_per_step")
        worst_step, worst_count = max(per_step.items(), key=lambda kv: (kv[1], -kv[0]))
        c["occurrences"] = sum(per_step.values())
        c["steps_affected"] = len(per_step)
        c["pages_affected"] = len({_page(s) for s in per_step})
        c["max_on_one_page"] = worst_count
        # Cite the worst page, not the first sighting: `step` and
        # `max_on_one_page` end up in the same sentence, so they must be true of
        # the same page. It is also the strongest evidence and the most useful
        # screenshot. evidence.py resolves the citation from here.
        c["step"] = worst_step
        if c["check"] == "image_missing_alt" and worst_step in image_places:
            c["where_on_worst_page"] = image_places[worst_step]
        if not c["examples"]:
            c.pop("examples")
        candidates.append(c)
    candidates.sort(key=lambda c: (-c["occurrences"], c["step"]))

    log.info("[a11y_checks] %d candidate(s) from %d snapshot(s) in %s",
             len(candidates), len(snapshots), store.trace_dir)
    pages_scanned = len({_page(s) for s, t in snapshots if isinstance(t, str)})
    return {"candidates": candidates, "steps_scanned": len(snapshots),
            "pages_scanned": pages_scanned}


# Topics the narrative writes WCAG issues about, keyed so a narrative issue can
# be joined back to the measured finding it came from. The a11y prompt fixes the
# vocabulary, so a short keyword list is more reliable here than fuzzy matching.
# Order matters: the first topic whose keyword appears wins. The vocabulary is
# fixed only loosely: "Brak dostępnej nazwy dla kontrolki" matched nothing,
# because the list had the words the other way round.
_TOPIC_KEYWORDS = (
    ("alt_text", ("alternatyw", "alt text", "atrybut alt", "atrybutu alt")),
    ("control_name", ("etykiet", "bez nazwy", "nazwy dostępnej", "dostępnej nazwy",
                      "nazwy kontrol", "nazwy dla kontrol", "accessible name")),
    ("landmark", ("orientacyjn", "landmark")),
    ("small_text", ("czcionk", "12px", "12 px", "rozmiar tekstu")),
    ("contrast", ("kontrast",)),
)


def a11y_topic(text: str) -> str | None:
    """Which measured check a WCAG sentence is about, if any."""
    low = (text or "").lower()
    for topic, words in _TOPIC_KEYWORDS:
        if any(w in low for w in words):
            return topic
    return None


def measure_names(store: TraceStore) -> dict:
    """How many images and controls were seen, and how many had no name.

    extract_a11y_candidates counts only the failures. Handed "images missing alt
    text on 13 of 13 pages" with no denominator, the narrator wrote that the
    configurator's product images lack alt text — in a run whose snapshots named
    611 of 678 images, every car by its model. The failures were real (icons
    inside already-labelled links); the claim built from them was not. The share
    is what stops a handful of icons being reported as a systemic barrier.
    """
    # Once per distinct page — the fullest snapshot of each URL, hash routes
    # kept apart since each configurator step is its own view. Summed over
    # every snapshot, a page revisited or stepped through was counted each
    # time: "145 z 1603 obrazów" for a run whose pages held at most ~424.
    pages: dict[str, dict] = {}
    for step, text in _steps(store.read_a11y_snapshots()):
        if not isinstance(text, str):
            continue
        page = {"images": {"total": 0, "unnamed": 0}, "controls": {"total": 0, "unnamed": 0}}
        page["images"].update({f"unnamed_{k}": v for k, v in classify_unnamed_images(text).items()})
        for n in parse_aria_snapshot(text):
            named = bool(n.name.strip())
            if n.role == "img":
                page["images"]["total"] += 1
                page["images"]["unnamed"] += not named
            elif n.role in _NAMED_CONTROLS:
                page["controls"]["total"] += 1
                page["controls"]["unnamed"] += not named
        try:
            url = store.url_at_step(step) or f"step {step}"
        except Exception:
            url = f"step {step}"
        if _page_size(page) > _page_size(pages.get(url)):
            pages[url] = page
    return summarise_pages(pages)


def _page_size(page: dict | None) -> int:
    return (page or {}).get("images", {}).get("total", 0) + (page or {}).get("controls", {}).get("total", 0)


def summarise_pages(pages: dict[str, dict]) -> dict:
    """Totals and shares over distinct pages, keeping the pages for merging."""
    images = {"total": 0, "unnamed": 0, "unnamed_in_named_control": 0,
              "unnamed_outside_content": 0, "unnamed_content": 0}
    controls = {"total": 0, "unnamed": 0}
    for page in pages.values():
        for bucket, key in ((images, "images"), (controls, "controls")):
            for k in bucket:
                bucket[k] += page[key].get(k, 0)
    for bucket in (images, controls):
        bucket["share_unnamed"] = (
            round(bucket["unnamed"] / bucket["total"], 3) if bucket["total"] else None
        )
    # The share that is a barrier: unnamed images carrying content, not icons
    # inside a named control or pixels outside the page.
    images["share_unnamed_content"] = (
        round(images["unnamed_content"] / images["total"], 3) if images["total"] else None)
    return {"images": images, "controls": controls, "pages": pages}


def merge_pages(results: list[dict]) -> dict:
    """measure_names over several sessions: each page once, its fullest capture."""
    merged: dict[str, dict] = {}
    for r in results:
        for url, page in (r.get("pages") or {}).items():
            if _page_size(page) > _page_size(merged.get(url)):
                merged[url] = page
    out = summarise_pages(merged)
    out.pop("pages")
    out["pages_counted"] = len(merged)
    return out
