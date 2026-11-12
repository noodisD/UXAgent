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

import functools
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

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
        \s*(?::.*)?$             # optional colon, then inline text: `- link "X": text`
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


# --- what the aria snapshot cannot say: tabindex, aria-hidden, the notice around a control ---
#
# The 2026-11-10 bnpparibas.pl run charged "poważny" for unnamed links. They were
# teaser image links (tabindex=-1, alt="") sitting beside a named link to the same
# address; the controls that really lack a name were a slider, two comboboxes and a
# text field, listed under the same line. The snapshot names which links are unnamed
# and where they point; raw_html says whether one is a redundant twin.

_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                        "link", "meta", "param", "source", "track", "wbr"})
_TEXTLESS_TAGS = frozenset({"script", "style", "template", "noscript"})
_NAME_ATTRS = ("aria-label", "aria-labelledby", "title")
# How far above a control a notice may sit: the reach consent_present gives it.
_CONSENT_REACH = 8
# Class words that make a no-alt <img> sized by CSS an icon, a backdrop or a loading indicator.
_ICONISH = ("icon", "tile", "background", "bgvisual", "loader", "spinner")
# What the page hides from view, which the snapshot (on-screen elements only) never lists.
_HIDDEN_CLASSES = frozenset({"hidden", "hide", "d-none", "is-hidden"})
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)

_SELECTOR = re.compile(r"^([a-z]*)(?:#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:(\*?=)'([^']*)'(\s+i)?)?\])$")


def _selector_rule(selector: str) -> dict | None:
    """One of the harness's consent selectors as a rule, None for a form it does not use.

    Only the simple forms the harness writes: tag, #id, .class, [attr], [attr='v'],
    [attr*='v'] and the case-insensitive variants."""
    m = _SELECTOR.match(selector.strip())
    if not m:
        return None
    tag, ident, cls, attr, op, val, ci = m.groups()
    if ident:
        return {"tag": tag, "attr": "id", "op": "=", "val": ident, "ci": False}
    if cls:
        return {"tag": tag, "attr": "class", "op": "~=", "val": cls, "ci": False}
    return {"tag": tag, "attr": attr, "op": op, "val": val, "ci": bool(ci)}


def _rule_hits(rule: dict, tag: str, attrs: dict) -> bool:
    if rule["tag"] and rule["tag"] != tag:
        return False
    have = attrs.get(rule["attr"])
    if have is None:
        return False
    if rule["op"] is None:
        return True
    want = rule["val"]
    if rule["ci"]:
        have, want = have.lower(), want.lower()
    if rule["op"] == "*=":
        return want in have
    if rule["op"] == "~=":
        return want in have.split()
    return have == want


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


class _Consent:
    """The harness's own idea of a consent notice (experiment.py), read from its constants.

    A container counts as a notice only when an acceptance control sits inside it, as
    consent_present requires: a name alone made pepco's footer cookie-policy link a banner,
    and a product modal with a bare close button is not a cookie notice. Without
    experiment.py's constants nothing is recognised, which leaves every control where it was."""

    def __init__(self):
        self.containers: list[dict] = []
        self.accepts: list[dict] = []
        self.consentish = None
        self.labels: frozenset = frozenset()
        try:
            from .experiment import (_CMP_SELECTORS, _CONSENT_CONTAINERS, _CONSENT_LABELS,
                                     _CONSENT_PRESENT_JS)
        except Exception:
            log.warning("[a11y_checks] consent selectors unavailable: consent notice items "
                        "are not told apart from the rest")
            return
        self.containers = [r for r in map(_selector_rule, _CONSENT_CONTAINERS) if r]
        self.accepts = [r for r in map(_selector_rule, _CMP_SELECTORS) if r]
        m = re.search(r"CONSENTISH\s*=\s*/([^/]+)/i", _CONSENT_PRESENT_JS)
        self.consentish = re.compile(m.group(1), re.I) if m else None
        self.labels = frozenset(_norm_text(label) for label in _CONSENT_LABELS)

    def container(self, tag: str, attrs: dict) -> bool:
        # The document root cannot be a notice, whatever class a script gave it.
        if tag in ("html", "body"):
            return False
        sig = f"{attrs.get('id', '')} {attrs.get('class', '')}"
        return (any(_rule_hits(r, tag, attrs) for r in self.containers)
                or bool(self.consentish and self.consentish.search(sig)))

    def accepts_control(self, tag: str, attrs: dict, text: str) -> bool:
        return (any(_rule_hits(r, tag, attrs) for r in self.accepts)
                or _norm_text(text) in self.labels)


@functools.lru_cache(maxsize=1)
def _consent() -> _Consent:
    return _Consent()


class _RawControls(HTMLParser):
    """Links, buttons and nameless images of one raw_html capture.

    `links` and `buttons` carry what decides a redundant twin (tabindex, aria-hidden, the
    images inside) or a consent item; `images` are the nameless <img> and <svg> that are
    not inside a control."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self.buttons: list[dict] = []
        self.images: list[dict] = []
        self._stack: list[dict] = []
        self._consent = _consent()

    def _control(self):
        for e in reversed(self._stack):
            if e["ctl"] is not None:
                return e["ctl"]
        return None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        top = self._stack[-1] if self._stack else None
        aria_hidden = bool(top and top["aria_hidden"]) or a.get("aria-hidden", "").strip().lower() == "true"
        role = a.get("role", "").strip().lower()
        presentational = bool(top and top["presentational"]) or role in ("presentation", "none")
        named_by_attr = any(a.get(k, "").strip() for k in _NAME_ATTRS)
        invisible = (bool(top and top["invisible"]) or "hidden" in a
                     or bool(_HIDDEN_STYLE.search(a.get("style", "")))
                     or bool(_HIDDEN_CLASSES & set(a.get("class", "").lower().split())))
        entry = {"tag": tag, "aria_hidden": aria_hidden, "presentational": presentational,
                 "invisible": invisible, "cls": a.get("class", "").lower(),
                 "cs": self._consent.container(tag, a), "accept": False, "ctl": None, "svg": None}
        parent_control = self._control()
        if tag == "img":
            alt = a["alt"] if "alt" in a else None
            if parent_control is not None:
                parent_control["imgs"].append({"alt": alt, "deco": presentational or aria_hidden,
                                               "named": named_by_attr})
            elif (alt is None and not named_by_attr and not invisible
                  and any(a.get(k) for k in ("src", "srcset", "data-src"))):
                near = " ".join([a.get("class", "").lower()] + [e["cls"] for e in self._stack[-3:]])
                css_sized = not a.get("width") and not a.get("height")
                self.images.append({"kind": "img", "decor": presentational or aria_hidden or (
                    css_sized and any(h in near for h in _ICONISH))})
        if tag in _VOID_TAGS:
            return
        if tag == "svg":
            if parent_control is None:
                entry["svg"] = {"named": named_by_attr}
            elif named_by_attr:
                parent_control["attr_named"] = True
        kind = ("link" if tag == "a" and "href" in a
                else "button" if tag == "button" or role == "button" else None)
        if kind:
            entry["ctl"] = {
                "kind": kind, "tag": tag, "attrs": a, "href": a.get("href", ""),
                "tabindex": a.get("tabindex", "").strip(), "aria_hidden": aria_hidden,
                "attr_named": named_by_attr, "imgs": [], "text": [],
                # The control's own class is not its container: "cookie-settings" on a link
                # says what it opens, not that it sits in a notice.
                "ctx": [e for e in self._stack[-(_CONSENT_REACH - 1):] if e["cs"]],
            }
        self._stack.append(entry)

    def handle_data(self, data):
        text = data.strip()
        if not text or not self._stack or self._stack[-1]["aria_hidden"]:
            return
        if any(e["tag"] in _TEXTLESS_TAGS for e in self._stack):
            return
        control = self._control()
        if control is not None:
            control["text"].append(text)
        for e in reversed(self._stack):
            if e["svg"] is not None:
                e["svg"]["named"] = True
                break

    def _pop(self):
        e = self._stack.pop()
        ctl = e["ctl"]
        if ctl is not None:
            ctl["text"] = " ".join(ctl["text"])
            if self._consent.accepts_control(ctl["tag"], ctl["attrs"], ctl["text"]):
                for ancestor in self._stack:
                    ancestor["accept"] = True
            if not e["invisible"]:
                (self.links if ctl["kind"] == "link" else self.buttons).append(ctl)
        if e["svg"] is not None and not e["svg"]["named"] and not e["invisible"]:
            self.images.append({"kind": "svg", "decor": True})

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["tag"] == tag:
                while len(self._stack) > i:
                    self._pop()
                return

    def finish(self):
        while self._stack:
            self._pop()


def _scan_raw(html: str) -> _RawControls | None:
    """The controls of a raw_html capture; None when there is none to read."""
    if not html or not html.strip():
        return None
    scan = _RawControls()
    try:
        scan.feed(html)
        scan.close()
    except Exception:
        log.warning("[a11y_checks] raw html could not be parsed", exc_info=True)
        return None
    scan.finish()
    return scan


def _yaml_scalar(raw: str) -> str:
    """The value of a `- /url: …` property: plain, "double" or 'single' quoted."""
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except ValueError:
            return raw.strip('"')
    if len(raw) >= 2 and raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    return raw


def _aria_controls(text: str) -> list[dict]:
    """Every link and button of a snapshot in order: {role, name, url}.

    `url` is the link's `/url:` property, None when the snapshot carries none."""
    lines = (text or "").splitlines()
    out = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        body = stripped[1:].strip()
        if body.startswith("/") or re.match(r"^(text|url)\s*:", body):
            continue
        m = _NODE.match(line)
        if not m or m.group("role") not in ("link", "button"):
            continue
        url = None
        if m.group("role") == "link":
            indent = len(line) - len(line.lstrip(" "))
            for nxt in lines[i + 1:i + 4]:
                if len(nxt) - len(nxt.lstrip(" ")) <= indent or not nxt.strip().startswith("- /"):
                    break
                if nxt.strip().startswith("- /url:"):
                    url = _yaml_scalar(nxt.strip()[len("- /url:"):])
                    break
        out.append({"role": m.group("role"), "name": (m.group("name") or "").strip(), "url": url})
    return out


def _href_key(href: str, base: str) -> str:
    """An address as the page and its snapshot both spell it, for pairing links."""
    ref = (href or "").strip()
    if not ref or ref.startswith(("#", "javascript:", "tel:", "mailto:")):
        return ref
    try:
        joined = urljoin(base, ref) if base else ref
    except ValueError:
        joined = ref
    return joined.rstrip("/") or "/"


def _names_destination(key: str) -> bool:
    """False for the placeholders a scripted control carries as its address ("#", "#!",
    "javascript:..."): two links that share one are not twins."""
    return bool(key.strip("#!")) and not key.lower().startswith("javascript:")


def _in_notice(ctl: dict) -> bool:
    return any(e["accept"] for e in ctl["ctx"])


def _named_by_image(ctl: dict) -> bool:
    return any(i["named"] or (i["alt"] and i["alt"].strip() and not i["deco"]) for i in ctl["imgs"])


def _raw_link_kind(ctl: dict) -> str | None:
    """"consent", "decorative" or "serious" for a nameless raw link, None when it has a name."""
    if ctl["text"].strip() or ctl["attr_named"]:
        return None
    nameless = not _named_by_image(ctl)
    if _in_notice(ctl) and nameless:
        return "consent"
    # Out of tab order or hidden from assistive technology: the keyboard and the screen
    # reader both skip it. An alt="" image does not do that: a link left in tab order with
    # no name is a keyboard stop with no name (WCAG F89), whatever else names the address.
    if ctl["tabindex"] == "-1" or ctl["aria_hidden"]:
        return "decorative"
    return "serious" if nameless else None


def _raw_button_kind(ctl: dict) -> str | None:
    if ctl["text"].strip() or ctl["attr_named"] or _named_by_image(ctl):
        return None
    return "consent" if _in_notice(ctl) else "serious"


def _classify(controls: list[dict], scan: _RawControls | None, base_url: str) -> list[dict]:
    """Each unnamed link or button of a snapshot with a `kind`, in document order.

      redundant — a link whose address another NAMED link on the page also has, that is
                  out of tab order or hidden from assistive technology (a placeholder
                  address such as "#" names no destination, so it never pairs)
      consent   — an unnamed link or button inside a consent notice
      serious   — an unnamed control nothing else on the page makes up for
      unknown   — the capture cannot say (none stored, no address in the snapshot, or more
                  unnamed controls than it accounts for); counted as serious

    The snapshot lists only what is on screen and raw_html everything the page holds, so
    a raw element is matched to a snapshot one only up to the number the snapshot shows.
    Where a serious element could account for it, the serious reading wins: a doubt never
    lowers what a real unnamed control costs."""
    unnamed = [c for c in controls if not c["name"]]
    if not unnamed:
        return []
    if scan is None:
        return [{**c, "kind": "unknown"} for c in unnamed]
    twins = {key for key in (_href_key(c["url"], base_url) for c in controls
                             if c["role"] == "link" and c["name"] and c["url"] is not None)
             if _names_destination(key)}
    links: dict[str, Counter] = {}
    for ctl in scan.links:
        kind = _raw_link_kind(ctl)
        if kind:
            links.setdefault(_href_key(ctl["href"], base_url), Counter())[kind] += 1
    buttons = Counter(filter(None, (_raw_button_kind(c) for c in scan.buttons)))
    out = []
    for c in unnamed:
        kind = "unknown"
        if c["role"] == "button":
            for raw_kind in ("serious", "consent"):
                if buttons[raw_kind]:
                    buttons[raw_kind] -= 1
                    kind = raw_kind
                    break
        elif c["url"] is not None:
            key = _href_key(c["url"], base_url)
            budget = links.get(key) or Counter()
            if budget["serious"]:
                budget["serious"] -= 1
                kind = "serious"
            elif budget["decorative"]:
                budget["decorative"] -= 1
                kind = "redundant" if key in twins else "serious"
            elif budget["consent"]:
                budget["consent"] -= 1
                kind = "consent"
        out.append({**c, "kind": kind})
    return out


def classify_unnamed_controls(text: str, raw_html: str, base_url: str = "") -> list[dict]:
    """_classify over one aria snapshot and the raw_html of the same step."""
    return _classify(_aria_controls(text), _scan_raw(raw_html), base_url)


def _step_url(store: TraceStore, step: int) -> str:
    try:
        return store.url_at_step(step) or ""
    except Exception:
        return ""


def _classify_step(store: TraceStore, step: int, text: str, base_url: str | None = None) -> list[dict]:
    controls = _aria_controls(text)
    if not any(not c["name"] for c in controls):
        return []
    base = _step_url(store, step) if base_url is None else base_url
    return _classify(controls, _scan_raw(store.read_raw_html(step)), base)


def classify_content_images(unnamed_content: int, scan: _RawControls | None) -> int | None:
    """How many of a page's unnamed content images are certainly decorative or unknown.

    Decorative: an <img> with no alt attribute that is a CSS-sized tile icon or backdrop, an
    inline <svg>, or inside an aria-hidden or presentation container; only those the page
    shows. The snapshot lists what is on screen and the markup everything, so icons in a
    footer the snapshot never listed cannot stand in for the photos it did. The plain
    no-alt images of the markup may be among the snapshot's, so only what they cannot
    account for counts. None without a raw capture.

    The finding is minor either way and the 20% rule that decides its severity is not
    touched; this only decides whether it may be worded as icons and backdrops."""
    if scan is None:
        return None
    decorative = sum(1 for i in scan.images if i["decor"])
    plain = len(scan.images) - decorative
    return min(decorative, max(0, unnamed_content - plain))


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

        # Redundant image twins and consent notice controls are reported on their own
        # (extract_unnamed_link_findings), not as unnamed controls of the site.
        moved = Counter((c["role"], c["kind"]) for c in _classify_step(store, step, text)
                        if c["kind"] in ("redundant", "consent"))
        moved_roles = {role for role, _kind in moved}
        for n in nodes:
            named = bool(n.name.strip())
            if n.role == "img" and not named:
                _add(("img_alt",), step, "image_missing_alt")
            elif n.role in _NAMED_CONTROLS and not named:
                for kind in ("redundant", "consent"):
                    if moved[(n.role, kind)]:
                        moved[(n.role, kind)] -= 1
                        break
                else:
                    _add(("ctl", n.role), step, "control_missing_name", role=n.role)
        for role, context in describe_unnamed_controls(text):
            entry = agg.get(("ctl", role))
            if entry is not None and role not in moved_roles \
                    and len(entry["examples"]) < _MAX_EXAMPLES_PER_CHECK \
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


# Names that say nothing about what a control does. The accessibility scan only
# asked whether a name EXISTS, so a price-info icon whose aria-label is
# "Pasek zadań" ("Taskbar") and a gallery button named "Flyout" passed it, while
# five of six participants in the 2026-09-30 hyundai run noticed them. Sighted
# users never see these strings; a screen reader says them instead of the
# control's purpose.
#
# Deliberately short, and only what was measured. Run over all 30,896 named
# controls in the 18 recorded runs, these rules flag 788 instances: "Pasek
# zadań" (753) and "Flyout" (35), nothing else. A technical-identifier rule
# (camelCase, snake_case) and a role-word-in-the-name rule ("checkbox X") were
# tried and matched nothing, so they are not here.
_JARGON_NAMES = frozenset({
    "taskbar", "pasek zadań", "pasek zadan", "flyout", "tooltip", "toggle",
    "button", "przycisk", "link", "icon", "ikona", "image", "obraz",
    "graphic", "grafika", "undefined", "null",
})
# Names specific enough to recognise in a participant's sentence. "button" or
# "link" would match nearly any text, so a finding is only pointed at those that
# cannot.
_DISTINCTIVE_NAMES = frozenset({"taskbar", "pasek zadań", "pasek zadan", "flyout"})

_ROLE_PL = {
    "button": "przycisk", "link": "link", "checkbox": "pole wyboru", "radio": "przycisk opcji",
    "combobox": "lista rozwijana", "textbox": "pole tekstowe", "searchbox": "pole wyszukiwania",
    "switch": "przełącznik", "slider": "suwak", "menuitem": "pozycja menu", "tab": "zakładka",
    "option": "opcja",
}


def generic_name_reason(role: str, name: str) -> str | None:
    """"jargon" when a control's accessible name describes nothing, else None."""
    n = (name or "").strip().lower()
    if n and role in _NAMED_CONTROLS and n in _JARGON_NAMES:
        return "jargon"
    return None


def generic_names_in_session(store: TraceStore) -> dict[str, str]:
    """{lower-case name: the name as the page spells it} for the distinctive
    non-descriptive names the session's pages had."""
    found: dict[str, str] = {}
    for _step, text in _steps(store.read_a11y_snapshots()):
        if isinstance(text, str):
            for n in parse_aria_snapshot(text):
                low = n.name.strip().lower()
                if generic_name_reason(n.role, n.name) and low in _DISTINCTIVE_NAMES:
                    found.setdefault(low, n.name.strip())
    return found


def extract_generic_name_findings(store: TraceStore) -> list[dict]:
    """One accessibility finding for the controls whose name describes nothing.

    Decided by code, not narrated by the model: the names are in the snapshots
    and the stoplist is fixed, so the finding needs no LLM and cannot be worded
    differently, dropped or duplicated by one. It is minor (drobny): the control
    has a name, just not a useful one.
    """
    from collections import Counter

    from .textutil import pl_plural, view_key

    per_step: dict[int, Counter] = {}
    all_pages: set[str] = set()

    def _page(step):
        # The same page under two query strings (a share code) is one page.
        try:
            return view_key(store.url_at_step(step)) or f"step {step}"
        except Exception:
            return f"step {step}"

    for step, text in _steps(store.read_a11y_snapshots()):
        if not isinstance(text, str):
            continue
        all_pages.add(_page(step))
        for n in parse_aria_snapshot(text):
            if generic_name_reason(n.role, n.name):
                per_step.setdefault(step, Counter())[(n.name.strip(), n.role)] += 1
    if not per_step:
        return []

    worst = max(per_step, key=lambda s: (sum(per_step[s].values()), -s))
    worst_total = sum(per_step[worst].values())
    pages_affected = len({_page(s) for s in per_step})
    items = per_step[worst].most_common()
    listing = ", ".join(f"„{name}” ({_ROLE_PL.get(role, role)}{f', {k}×' if k > 1 else ''})"
                        for (name, role), k in items[:4])
    if len(items) > 4:
        more = len(items) - 4
        listing += f" i {more} {pl_plural(more, 'inna', 'inne', 'innych')}"
    sub = pl_plural(worst_total, "kontrolka ma", "kontrolki mają", "kontrolek ma")
    their = "jej" if worst_total == 1 else "ich"
    spread = ("Problem występuje na jedynej odwiedzonej stronie." if len(all_pages) == 1 else
              f"Problem występuje na {pages_affected} z {len(all_pages)} odwiedzonych stron.")
    detail = (f"Na najgorszej stronie (krok {worst}) {worst_total} {sub} nazwę, która nie opisuje "
              f"{their} działania: {listing}. {spread} Czytnik ekranu odczyta tę nazwę zamiast "
              "opisu funkcji.")
    names: dict[tuple, set] = {}
    for step, counter in per_step.items():
        for key in counter:
            names.setdefault(key, set()).add(_page(step))
    return [{
        "category": "Nieopisowa nazwa kontrolki",
        "severity": "drobny",
        "topic": "generic_name",
        "step": worst,
        "url": store.url_at_step(worst),
        "detail": detail,
        "recommendation": ("Nadać tym kontrolkom nazwy opisujące ich działanie (aria-label albo "
                           "widoczny tekst) zamiast nazw technicznych."),
        "generic_names": [{"name": n, "role": r, "pages": len(p)} for (n, r), p in sorted(names.items())],
        "deterministic": True,
    }]


# The closing sentence of those two findings, which says how widespread the item is, with
# the nouns it counts and the measured.controls keys of the same count across the audit.
_SPREAD_NOUNS = {
    "redundant_link": ("taki link", "takie linki", "takich linków"),
    "consent_notice": ("taki element", "takie elementy", "takich elementów"),
}
_SPREAD_MEASURED = {
    "redundant_link": ("unnamed_redundant_link", "pages_with_unnamed_redundant_link"),
    "consent_notice": ("unnamed_consent", "pages_with_unnamed_consent"),
}
_SPREAD_TAIL = re.compile(r"\s*(?:Łącznie \d+ [^.]+? na \d+ z \d+ odwiedzonych stron\."
                          r"|Występuje na jedynej odwiedzonej stronie\.)\s*$")


def _spread_sentence(topic: str, total: int, pages: int, all_pages: int) -> str:
    from .textutil import pl_plural

    if all_pages == 1:
        return "Występuje na jedynej odwiedzonej stronie."
    return (f"Łącznie {total} {pl_plural(total, *_SPREAD_NOUNS[topic])} na {pages} z {all_pages} "
            "odwiedzonych stron.")


def restate_spread(findings: list[dict], measured: dict | None) -> None:
    """Give the merged redundant-link and consent entries the audit's own count.

    The aggregate keeps the first participant's detail, whose closing sentence counts that
    participant's pages ("6 takich linków na 2 z 9 stron") beside the score line and
    measured.controls that count every participant's, once per page (26 on 10 pages)."""
    controls = (measured or {}).get("controls") or {}
    all_pages = (measured or {}).get("pages_counted") or 0
    for f in findings:
        keys = _SPREAD_MEASURED.get(f.get("topic"))
        tail = _SPREAD_TAIL.search(str(f.get("detail") or ""))
        total = controls.get(keys[0]) if keys else 0
        if not (total and all_pages and tail):
            continue
        f["detail"] = (f["detail"][:tail.start()] + " "
                       + _spread_sentence(f["topic"], total, controls.get(keys[1], 0), all_pages)).strip()


def extract_unnamed_link_findings(store: TraceStore) -> list[dict]:
    """Findings for unnamed controls that are not unnamed controls of the site.

    A link that only duplicates a named link to the same address and that the keyboard and
    the screen reader both skip (out of tab order or hidden), and the bare links and buttons
    of a consent notice. Decided by code from the snapshot and raw_html and reported as minor
    (drobny), each on its own: they are no reason to call the site's controls unnamed
    ("poważny"), and the narrator is not handed them as candidates.

    Counted once per page and element: the same page snapshotted again, or opened by
    another participant, is the same links. Both the instances and the pages appear.
    """
    from .textutil import pl_plural, view_key

    per_step: dict[str, dict[int, Counter]] = {"redundant": {}, "consent": {}}
    elements: dict[str, dict[tuple, int]] = {"redundant": {}, "consent": {}}
    all_pages: set[str] = set()
    for step, text in _steps(store.read_a11y_snapshots()):
        if not isinstance(text, str):
            continue
        url = _step_url(store, step)
        page = view_key(url) or f"step {step}"
        all_pages.add(page)
        for c in _classify_step(store, step, text, url):
            if c["kind"] not in per_step:
                continue
            per_step[c["kind"]].setdefault(step, Counter())[(c["role"], c["url"] or "")] += 1
        for kind, steps in per_step.items():
            for (role, ident), k in steps.get(step, Counter()).items():
                key = (page, role, ident)
                elements[kind][key] = max(elements[kind].get(key, 0), k)

    def spread(kind: str, topic: str) -> str:
        return _spread_sentence(topic, sum(elements[kind].values()),
                                len({p for p, _role, _ident in elements[kind]}), len(all_pages))

    def worst_of(kind: str) -> tuple[int, Counter]:
        steps = per_step[kind]
        worst = max(steps, key=lambda s: (sum(steps[s].values()), -s))
        return worst, steps[worst]

    out = []
    if per_step["redundant"]:
        worst, here = worst_of("redundant")
        k = sum(here.values())
        sub = pl_plural(k, "link graficzny nie ma", "linki graficzne nie mają",
                        "linków graficznych nie ma")
        addresses = ", ".join(f"„{ident[:70]}”" for (_role, ident), _n in here.most_common(3))
        out.append({
            "category": "Zduplikowany link graficzny bez nazwy",
            "severity": "drobny",
            "topic": "redundant_link",
            "step": worst,
            "url": store.url_at_step(worst),
            "detail": (f"Na najgorszej stronie (krok {worst}) {k} {sub} nazwy dostępnej, ale każdy "
                       f"z nich dubluje nazwany link do tego samego adresu ({addresses}) i jest poza "
                       "kolejnością tabulatora albo ukryty przed technologiami wspomagającymi. Adres "
                       "jest dostępny przez nazwany link, a pusty duplikat tylko wydłuża listę "
                       "linków czytnika ekranu. "
                       + spread("redundant", "redundant_link")),
            "recommendation": ("Ukryć duplikat przed technologiami wspomagającymi (aria-hidden=\"true\" "
                               "i tabindex=\"-1\") albo włączyć obraz do nazwanego linku; dodanie "
                               "aria-label sprawiłoby, że czytnik ekranu odczyta ten sam cel dwa razy."),
            "instances": sum(elements["redundant"].values()),
            "pages_affected": len({p for p, _role, _ident in elements["redundant"]}),
            "deterministic": True,
        })
    if per_step["consent"]:
        worst, here = worst_of("consent")
        k = sum(here.values())
        listing = ", ".join(f"{_ROLE_PL.get(role, role)}{f' ({n}×)' if n > 1 else ''}"
                            for (role, _ident), n in here.most_common(3))
        out.append({
            "category": "Baner zgody na pliki cookie: elementy bez nazwy",
            "severity": "drobny",
            "topic": "consent_notice",
            "step": worst,
            "url": store.url_at_step(worst),
            "detail": (f"W banerze zgody na pliki cookie (krok {worst}) "
                       f"{k} {pl_plural(k, 'element nie ma', 'elementy nie mają', 'elementów nie ma')} "
                       f"nazwy dostępnej: {listing}. To elementy samego banera zgody, a nie "
                       "nawigacji ani treści strony. "
                       + spread("consent", "consent_notice")),
            "recommendation": ("Nadać tym elementom nazwę (aria-label albo widoczny tekst) w "
                               "konfiguracji narzędzia zgód albo usunąć z banera pusty link."),
            "instances": sum(elements["consent"].values()),
            "pages_affected": len({p for p, _role, _ident in elements["consent"]}),
            "deterministic": True,
        })
    return out


# Topics the narrative writes WCAG issues about, keyed so a narrative issue can
# be joined back to the measured finding it came from. The a11y prompt fixes the
# vocabulary, so a short keyword list is more reliable here than fuzzy matching.
# Order matters: the first topic whose keyword appears wins. The vocabulary is
# fixed only loosely: "Brak dostępnej nazwy dla kontrolki" matched nothing,
# because the list had the words the other way round.
_TOPIC_KEYWORDS = (
    # Before control_name: both say "bez nazwy", and these are not the site's unnamed controls.
    ("consent_notice", ("baner zgody", "baneru zgody", "banerze zgody")),
    ("redundant_link", ("zduplikowany link graficzny", "zduplikowane linki graficzne",
                        "zduplikowanych linków graficznych")),
    ("generic_name", ("nieopisow", "nie opisuje ich działania", "nie opisuje jej działania")),
    ("alt_text", ("alternatyw", "alt text", "atrybut alt", "atrybutu alt")),
    # "brakuje nazwy dla 2 przycisków" and "bez przypisanej nazwy" were missing: the
    # same defect class (a control with no accessible name) was one topic when the
    # model wrote "bez nazwy dostępnej" and none when it wrote "brakuje nazwy dla…",
    # so the 2026-10-01 report listed unnamed buttons twice — as their own finding
    # and again under the combobox's — and deducted twice.
    ("control_name", ("etykiet", "bez nazwy", "nazwy dostępnej", "dostępnej nazwy",
                      "nazwy kontrol", "nazwy dla kontrol", "accessible name",
                      "brakuje nazwy", "brak nazwy", "brak nazw", "przypisanej nazwy",
                      "bez nazw")),
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
    fullest: dict[str, tuple[int, str]] = {}
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
            pages[url], fullest[url] = page, (step, text)
    # Told apart only for the capture that is kept, and only where raw_html was stored.
    for url, (step, text) in fullest.items():
        _split_unnamed(store, step, text, pages[url])
    return summarise_pages(pages)


def _split_unnamed(store: TraceStore, step: int, text: str, page: dict) -> None:
    """Which of a page's unnamed controls are redundant twins or consent notice items, and
    how many unnamed content images are decorative or unknown. Totals are not touched."""
    controls, images = page["controls"], page["images"]
    if not controls["unnamed"] and not images.get("unnamed_content"):
        return
    scan = _scan_raw(store.read_raw_html(step))
    if scan is None:
        return
    split = Counter(c["kind"] for c in _classify(_aria_controls(text), scan, _step_url(store, step)))
    controls["unnamed_redundant_link"] = split["redundant"]
    controls["unnamed_consent"] = split["consent"]
    decorative = classify_content_images(images.get("unnamed_content", 0), scan)
    if decorative is not None:
        images["unnamed_content_decorative"] = decorative


def _page_size(page: dict | None) -> int:
    return (page or {}).get("images", {}).get("total", 0) + (page or {}).get("controls", {}).get("total", 0)


def summarise_pages(pages: dict[str, dict]) -> dict:
    """Totals and shares over distinct pages, keeping the pages for merging."""
    images = {"total": 0, "unnamed": 0, "unnamed_in_named_control": 0,
              "unnamed_outside_content": 0, "unnamed_content": 0,
              "unnamed_content_decorative": 0}
    controls = {"total": 0, "unnamed": 0, "unnamed_redundant_link": 0, "unnamed_consent": 0}
    for page in pages.values():
        for bucket, key in ((images, "images"), (controls, "controls")):
            for k in bucket:
                bucket[k] += page[key].get(k, 0)
    for bucket in (images, controls):
        bucket["share_unnamed"] = (
            round(bucket["unnamed"] / bucket["total"], 3) if bucket["total"] else None
        )
    # Instances and pages both: the unnamed controls that are neither a redundant twin nor
    # a consent notice item, and on how many distinct pages each kind sits.
    def kinds(c: dict) -> dict:
        redundant, consent = c.get("unnamed_redundant_link", 0), c.get("unnamed_consent", 0)
        return {"redundant_link": redundant, "consent": consent,
                "serious": c.get("unnamed", 0) - redundant - consent}

    controls["unnamed_serious"] = kinds(controls)["serious"]
    for name in ("redundant_link", "consent", "serious"):
        controls[f"pages_with_unnamed_{name}"] = sum(
            1 for p in pages.values() if kinds(p.get("controls") or {})[name] > 0)
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
