"""Harness-vs-site attribution for audit findings.

The auditor LLM turns the agent's session into UX findings, but it cannot tell
the difference between "the site is broken" and "the agent failed to interact
with the site." Both look the same to it: an action that produced no visible
change. In practice a large share of severe findings trace back to *harness*
interaction failures — e.g. pepco.pl's Google Places store locator (typed text
never selected a suggestion) or a product search the agent submitted with Enter
that the site only submits on button click. Those got reported as CRITICAL site
defects ("store locator non-functional", "search doesn't process queries").

This module flags findings whose cited step coincides with a harness
interaction failure, so they can be excluded from the headline severity counts
and rendered as "needs verification" rather than shipped as confirmed defects.
"""
import html as _html
import logging
import re

from .evidence import _QUOTED, _visible_text
from .trace_store import TraceStore, parse_action as _parse_action

log = logging.getLogger(__name__)

# The harness's own fallback description, emitted when an element "didn't work
# after several tries" (experiment.py: "Ten element nie działał mimo kilku prób —
# wracam i wybieram inną drogę."). Its presence at a step is direct proof the
# interaction failed on the harness side, not the site side.
#
# Only that sentence. The markers used to be fragments of it ("nie działał",
# "wybieram inną", "innej drogi"), so a persona's ordinary "Wersja Smart jest za
# droga, wybieram inną wersję wyposażenia" marked its step a tool failure and held
# every finding cited there. Over all 626 recorded actions the old fragments
# matched 10, every one of them the harness's own sentence, so this changes
# nothing on recorded data.
_FALLBACK_MARKERS = ("nie działał mimo kilku prób",)

# The same (action, target) repeated this many times *consecutively* is a stuck
# loop: the agent retried back-to-back because nothing changed.
#
# It must be consecutive. Counting repeats across the whole session flags normal
# behaviour — in run 2026-07-16_13-41-14 Marcin typed six different queries into
# the same search box and Michał revisited one category three times; neither is a
# loop, yet the session-wide rule flagged 7 of Marcin's 9 real findings as
# "needs verification" and dropped them from the headline counts.
_REPEAT_THRESHOLD = 3




# Perception-artifact signature: the observation text says (almost) nothing
# while the step's raw HTML is large — the perceiver failed on an overlay
# state; the page was not actually blank. Three "The page is empty" CRITICAL
# findings in the diabetyk24 ground-truth run were exactly this.
_ARTIFACT_OBS_MAX_CHARS = 80
_ARTIFACT_RAW_MIN_BYTES = 20_000

# Errors the executor writes when it could not carry out an action at all
# (executor/env.py). Their presence is direct proof the failure was ours: a
# genuine site error — a 404, a validation message, a dead button — arrives as
# page *content*, never through this channel.
#
# Deliberately NOT included: the repeat- and revisit-advisories that
# experiment.py injects into the same field. Those say the agent retried
# something, which is just as likely to mean the *site* did not respond — the
# exact real defect this module must not swallow. Consecutive repeats are
# already covered, more conservatively, by _REPEAT_THRESHOLD below.
_EXECUTOR_ERROR_MARKERS = (
    "error executing action:",
    "invalid json action format:",
    "missing required parameter in action:",
)


# Bot-detection interstitials. A WAF challenge page — Cloudflare, DataDome,
# PerimeterX, Imperva — is the automation being *detected*, which is the one
# thing a real visitor almost never sees. The first external review's hebe.pl
# report was six critical site defects generated entirely from such a screen,
# including a recommendation that the site put a timeout on its own bot
# protection. None of the existing signatures can see it: a challenge page
# loads successfully and renders text, so there is no executor error, no empty
# observation and no consent state — it is ordinary page *content*.
#
# Matched as whole phrases, never on a bare "bot" or "captcha": a drugstore
# selling robot vacuums, or any page explaining its cookie and bot policy,
# would otherwise condemn its own session.
_BOT_WALL_MARKERS = (
    "performing verification",
    "checking your browser",
    "verify you are human",
    "verifying you are human",
    "are not a bot",
    "is not a robot",
    "i'm not a robot",
    "service to protect against malicious bots",
    "ddos protection by",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "please stand by, while we are checking your browser",
    "additional security check is required",
    "weryfikacja przeglądarki",
    "sprawdzamy, czy nie jesteś robotem",
    "potwierdź, że nie jesteś robotem",
)


def bot_wall_steps(store: TraceStore) -> set[int]:
    """Steps whose page was a bot-detection challenge rather than the site.

    Read from the observation text because that is the only place a challenge
    page shows up — it is served with a normal status and renders normally.
    """
    blocked: set[int] = set()
    try:
        n_steps = len(store.read_action_trace() or [])
    except Exception:
        n_steps = 0
    for i in range(n_steps):
        try:
            text = store.read_observation_step_text(i).lower()
        except Exception:
            continue
        if any(m in text for m in _BOT_WALL_MARKERS):
            blocked.add(i)
    return blocked


def session_blocked_by_bot_wall(store: TraceStore) -> bool:
    """True when the session never got past a challenge page.

    The distinction that matters for the run as a whole: a challenge on one
    step is noise to be gated, but a session where *every* step was the
    challenge observed nothing about the site at all, and no usability verdict
    can rest on it. That is the same situation as a page that never loaded,
    and it is handled the same way — an explicit failure, not a score.
    """
    try:
        n_steps = len(store.read_action_trace() or [])
    except Exception:
        return False
    if n_steps == 0:
        return False
    return len(bot_wall_steps(store)) == n_steps


def why_site_unseen(store: TraceStore) -> str | None:
    """Why this session observed nothing of the site, in Polish — None if it did.

    Such a session is no participant: its "failed" task, its 5/5 accessibility
    pass over nothing and its survey answers are about our tool. run.py used to
    apply this only when every session missed the site; in a mixed run the dead
    sessions counted everywhere (1 of 4 turned 1dcb's accessibility average from
    1.0 to 2.0 and its score from 4.0 to 3.8, and charged a failed task for a
    page nobody saw). hebe blocks only some sessions, so mixed runs are real.
    """
    try:
        steps = int((store.read_session_result() or {}).get("steps_taken") or 0)
    except (TypeError, ValueError):
        steps = 0
    if steps <= 0:
        return "sesja nie wykonała żadnego kroku — strona się nie załadowała"
    try:
        if session_blocked_by_bot_wall(store):
            return "cała sesja na ekranie weryfikacji antybotowej, nie na stronie"
    except Exception:
        pass
    return None


def _executor_error_detail(err: str) -> str:
    """The executor's error in a few Polish words, for the reader of the report."""
    e = err.lower()
    if "intercepts pointer events" in e:
        return "kliknięcie zablokowała inna warstwa strony"
    if "timeout" in e:
        if "page.goto" in e or "navigat" in e:
            return "strona nie załadowała się w limicie czasu"
        if "locator" in e:
            return "element nie zareagował w limicie czasu"
        return "akcja przekroczyła limit czasu"
    if "not found" in e or "nie znaleziono" in e:
        return "element nie został znaleziony na stronie"
    return "akcja nie została wykonana"


def harness_suspect_steps(store: TraceStore, progress_runs: bool = True) -> set[int]:
    """Step indices whose action is a harness interaction failure.

    See harness_suspect_causes for the six signatures. This is the set of steps;
    that one also says which signature marked each."""
    return set(harness_suspect_causes(store, progress_runs))


def harness_suspect_causes(store: TraceStore, progress_runs: bool = True) -> dict[int, tuple[str, str]]:
    """Step index -> (cause code, detail) for every step marked as a harness failure.

    Six signatures, all deterministic, tried in this order (the first to mark a
    step names it):
      executor_error  the executor reported it could not perform the action at all;
      consent         a consent notice was on screen and never dismissed;
      bot_wall        the page was a bot-detection challenge rather than the site;
      perception_gap  a near-empty observation over a large raw page — the perceiver
                      failed on an overlay state, the page was not actually blank;
      fallback        a fallback action ("...nie działał mimo kilku prób...") — and
                      the step it retreated from, where the finding usually anchors;
      repeat          an (action, target) pair repeated >= _REPEAT_THRESHOLD times —
                      a stuck loop where the agent retried because nothing changed.

    The cause is what lets the report say why a finding was held. Every one used
    to read "prawdopodobna awaria narzędzia, nie strony", which overclaims: a
    copy-text remark made on a step that carried a blocked click is held because
    of the click, not because the remark is false.
    """
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    causes: dict[int, tuple[str, str]] = {}

    def mark(i: int, code: str, detail: str = "") -> None:
        if i >= 0:
            causes.setdefault(i, (code, detail))

    # Executor failures. observations[i] is the state recorded *before*
    # action i (experiment.py appends at the top of the loop), so the error it
    # carries was produced by action i-1 — that is the step to blame. Step i is
    # marked too: the agent perceives the un-changed page there, and that is
    # usually where the resulting finding anchors.
    #
    # This is the strongest harness-vs-site signal in the run and it was written
    # to disk every session while nothing read it. It is what turned pepco.pl's
    # consent overlay into "critical site defects".
    for i, obs in enumerate(store.read_observations() or []):
        if not isinstance(obs, dict):
            continue
        err = str(obs.get("error") or "")
        if any(m in err.lower() for m in _EXECUTOR_ERROR_MARKERS):
            detail = _executor_error_detail(err)
            mark(i - 1, "executor_error", detail)
            mark(i, "executor_error", detail)

    # A consent notice was on screen and would not go away, so every
    # interaction from that point may have been intercepted by the overlay.
    # Nothing downstream could see this before: dismiss_cookie_banners returned
    # a bare bool and recorded nothing.
    try:
        unresolved = store.unresolved_consent_steps()
    except Exception:
        unresolved = set()
    # From the blocked step to the first later one recorded as dismissed or
    # gone — that step included, since the persona perceives the aftermath
    # there. It used to run to the end regardless, so one failed dismissal, or
    # a banner the persona then accepted itself, gated every later finding.
    # With no such record (every archived session: "gone" was never written)
    # it still runs to the end: consent_present does not recognise every
    # notice the landing dismissal tries, so a missing record is no proof the
    # notice went away.
    if unresolved:
        states = store.read_consent_states() or {}
        n_steps = len(actions)
        for start in sorted(unresolved):
            end = next((k for k in sorted(states)
                        if k > start and isinstance(states[k], dict)
                        and (states[k].get("dismissed") or states[k].get("present") is False)),
                       None)
            for k in range(start, n_steps if end is None else min(end + 1, n_steps)):
                mark(k, "consent")

    # Bot-detection challenge pages. Anything the auditor concludes from a
    # screen that exists because we are automated is about us, not the site.
    for k in bot_wall_steps(store):
        mark(k, "bot_wall")

    for i in range(len(actions)):
        obs = store.read_observation_step_text(i).strip()
        if obs and len(obs) <= _ARTIFACT_OBS_MAX_CHARS:
            if store.raw_html_size(i) >= _ARTIFACT_RAW_MIN_BYTES:
                mark(i, "perception_gap")

    for i, a in enumerate(actions):
        desc = (a.get("description") or "").lower()
        if any(m in desc for m in _FALLBACK_MARKERS):
            mark(i, "fallback")
            mark(i - 1, "fallback")

    # Consecutive runs of the same (action, target). With progress_runs=False,
    # a run whose page changed after every click is left out (see
    # progress_run_steps): only the wording of what it holds back differs.
    for start, end, target in _repeat_runs(actions):
        if progress_runs or not _page_changed_throughout(store, start, end):
            for k in range(start, end):
                mark(k, "repeat", str(target or ""))

    return causes


def _repeat_runs(actions: list) -> list[tuple[int, int, str]]:
    """(start, end, target) of every run of >= _REPEAT_THRESHOLD identical clicks."""
    runs, run_start = [], 0
    for i in range(1, len(actions) + 1):
        prev = actions[i - 1]
        same = (
            i < len(actions)
            and prev.get("target")
            and actions[i].get("target") == prev.get("target")
            and actions[i].get("action") == prev.get("action")
        )
        if same:
            continue
        if i - run_start >= _REPEAT_THRESHOLD and prev.get("target"):
            runs.append((run_start, i, str(prev.get("target"))))
        run_start = i
    return runs


def _step_text(store: TraceStore, step: int) -> str | None:
    path = store.trace_dir / "simp_html" / f"simp_html_{step}.html"
    try:
        return _visible_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def _page_changed_throughout(store: TraceStore, start: int, end: int) -> bool:
    """Did the page's visible text change after every action of the run?"""
    texts = [_step_text(store, k) for k in range(start, end + 1)]
    if any(t is None for t in texts):
        return False  # cannot show progress, so it is not assumed
    return all(a != b for a, b in zip(texts, texts[1:]))


def progress_run_steps(store: TraceStore) -> dict[int, str]:
    """Steps of repeated clicks that changed the page every time -> the control.

    The repeat rule reads three identical clicks as a stuck loop. On the
    configurator's colour step, "next" moved the exterior-view gallery on
    every click (Slide 1 -> 4 of 8) and "Następny krok" advanced the wizard:
    nothing was stuck. Findings there are still held — one of the three in the
    archive was false, one debatable — but not as a failure of this tool.
    """
    actions = [_parse_action(a) or {} for a in (store.read_action_trace() or [])]
    out: dict[int, str] = {}
    for start, end, target in _repeat_runs(actions):
        if _page_changed_throughout(store, start, end):
            label = _on_screen_label(store, start, target) or target
            out.update({i: label for i in range(start, end)})
    return out


def _on_screen_label(store: TraceStore, step: int, semantic_id: str) -> str:
    """The text the page shows on the element, not its parser id ("next" -> "Next")."""
    path = store.trace_dir / "simp_html" / f"simp_html_{step}.html"
    try:
        markup = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r'<(\w+)[^>]*parser-semantic-id="' + re.escape(semantic_id) + r'"[^>]*>(.*?)</\1>',
                  markup, re.S)
    text = _visible_text(m.group(2)) if m else ""
    return text if 0 < len(text) <= 60 else ""


# Phrases a finding uses to claim the product search itself failed. If the
# trace proves the search *did* run (reached a /search results URL), such a
# claim is contradicted by the agent's own navigation and is harness-suspect.
_SEARCH_FAIL_MARKERS = (
    "nie przetwarza", "nie rozumie", "nie zwraca", "nie wyświetl",
    "brak wyników", "wyszukiwarka nie", "wyszukiwarki nie",
    "nie przetworzy", "nie zadział",
)


def _search_ran(store: TraceStore) -> bool:
    """True if the session ever reached a product-search results URL."""
    try:
        urls = store.visited_urls()
    except Exception:
        urls = []
    return any("/search" in (u or "").lower() for u in urls)


def _claims_search_broken(finding: dict) -> bool:
    text = f"{finding.get('evidence', '')} {finding.get('recommendation', '')}".lower()
    return "wyszukiwar" in text and any(m in text for m in _SEARCH_FAIL_MARKERS)


def _suspect_targets(store: TraceStore, suspect: set[int]) -> set[str]:
    """Element targets (parser-semantic-ids) involved in the failed interactions.
    Findings often anchor on a later step (e.g. the terminate) but name the
    element in their evidence text — e.g. Kacper's "pola wyszukiwania
    'wyszukaj_lokalizacje'" at step 28, after the loop ended at step 24."""
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    targets = {
        str(actions[i].get("target"))
        for i in suspect
        if i < len(actions) and actions[i].get("target")
    }
    # Ids shorter than this carry no signal: matched against Polish prose, a
    # target like "1", "q" or "cart" hits unrelated findings and demotes them to
    # harness_suspect, which zeroes severity_score, drops them from headline
    # counts and the prioritization matrix, and caps confidence to low. The
    # match is only meaningful for distinctive parser ids ("wyszukaj_lokalizacje").
    #
    # The review plan proposed removing this floor, so short consent ids ("ok",
    # "zgoda") could be flagged by name. Measured over the 28 archived sessions
    # it condemns 2 further findings out of 135 — both matched through the ids
    # "1" and "2" landing on digits in prose ("Koszyk, 2 artykuły"), neither
    # about consent. The floor stays: consent no longer needs the name path,
    # because unresolved banners are caught per step from write_consent_states,
    # which is both stronger and not prose-dependent. See
    # test_the_target_floor_is_measured_not_assumed.
    return {t for t in targets if len(t) >= _MIN_TARGET_LEN}


_MIN_TARGET_LEN = 5


def _names_target(text: str, targets: set[str]) -> bool:
    """Does the finding's prose name one of these elements?

    Whole-token match, not a substring: these are identifiers quoted inside the
    text ("pola wyszukiwania 'wyszukaj_lokalizacje'"), so a bare `t in text`
    also fired on any word that merely contained the id. Unlike the Polish stem
    markers in session_goal_state, a full boundary is correct here — an
    identifier is not inflected.
    """
    return any(
        re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text, re.IGNORECASE)
        for t in targets
    )


# Perception loss: the page had the content, our simplifier dropped it.
#
# The lidl.pl re-run produced three findings that carried the whole report —
# "the page says 10 products and shows none", two of them critical — and all
# three were artifacts. raw_html at the cited steps holds 18 and 22 price
# strings; the simplified observation the agent saw holds none. The product
# grid never reached the agent.
#
# Worse, the claim verifier confirmed them. It re-reads the cited step's
# observation, which is the same simplified text that produced the claim, so a
# perception failure certifies itself: claim_verdict "supported", confidence
# high, no gate. Raw HTML is the only record that does not pass through the
# simplifier, which is what makes it usable as independent evidence here.
#
# Prices are the signal because they are unambiguous and locale-specific: a
# listing page that showed money has content, whatever else is arguable. The
# check is deliberately one-directional — it only ever exonerates the site.
_PRICE_RE = re.compile(r"\d+[,.]\d{2}\s*(?:zł|PLN|EUR|€)", re.I)
_PRICES_FOR_LOSS = 5

# Claims of the form "the content is not there". Only these are affected: a
# step that lost its product grid can still host a perfectly good finding about
# something else, and suppressing that would be over-reach.
_ABSENCE_MARKERS = (
    "nie jest widoczn", "nie są widoczn", "niewidoczn", "nie wyświetla",
    "nie pokazuje", "nie widać", "brak produkt", "brak listy", "pusta lista",
    "żadnej listy", "żadnych produkt", "nie załadowa", "nie renderuj",
)


def perception_loss_steps(store: TraceStore) -> set[int]:
    """Steps where the raw page carried prices and the observation carried none.

    Evidence that the agent was shown less than the visitor would have been, so
    "I could not see it" says something about this tool, not about the site.
    """
    lost: set[int] = set()
    try:
        n_steps = len(store.read_action_trace() or [])
    except Exception:
        return lost
    for i in range(n_steps):
        try:
            raw = store.read_raw_html(i)
        except Exception:
            continue
        if not raw or len(_PRICE_RE.findall(raw)) < _PRICES_FOR_LOSS:
            continue
        try:
            seen = store.read_observation_step_text(i)
        except Exception:
            seen = ""
        if not _PRICE_RE.search(seen or ""):
            lost.add(i)
    return lost


def _claims_content_absent(finding: dict) -> bool:
    text = f"{finding.get('evidence', '')} {finding.get('recommendation', '')}".lower()
    return any(m in text for m in _ABSENCE_MARKERS)


# Text a finding quotes that the persona was shown but the site never showed.
#
# The persona does not read the page: a model transcribes each page's HTML into
# the observation it acts on, and that transcription drifts. In the 2026-09-25
# hyundai replay it rendered the Polish menu in Spanish, and the report carried
# a verified serious finding, "Serwis wyświetla treści w języku hiszpańskim",
# quoting "Todos SUV Eléctrico Híbrido…" — words on no page of the session.
# Two more of the same kind in the archive: "angielskie etykiety, takie jak
# 'Close button' i 'Year of production'", and a search "showing 'No results'"
# on a page that said "Brak wyników". perceive.txt now asks for the page's own
# words, which makes this rare; this check catches what still gets through.
#
# The quote must be absent from every page of the session — visible text and
# the attributes a person perceives as text — and present in the observation,
# so the rule only ever blames the transcription it can prove was wrong.
# Measured on the archive: 3 of 34 findings with quotes, all three the cases
# above. Two near misses shaped it: "Zamknij" and "Pasek zadań" are real, as
# title and aria-label; "Twój Hyundai TUCSONMODERN PLUS" is real too, with the
# observation running two elements together, hence whitespace is ignored.
_TEXT_ATTR_RE = re.compile(
    r'\b(?:title|aria-label|alt|placeholder|value|aria-placeholder|label)\s*=\s*"([^"]*)"',
    re.I)
_TRAILING_ELLIPSIS_RE = re.compile(r"(?:\.\.\.|…)+\s*$")


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def session_page_text(store: TraceStore) -> str:
    """Everything the site showed a visitor over the session, whitespace removed."""
    parts: list[str] = []
    for sub in ("simp_html", "raw_html"):
        for path in sorted((store.trace_dir / sub).glob("*.html")):
            try:
                markup = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parts.append(_visible_text(markup))
            parts.extend(_TEXT_ATTR_RE.findall(markup))
    return _squeeze(_html.unescape(" ".join(parts)))


def session_observation_text(store: TraceStore) -> str:
    """What the persona was shown, over the session, whitespace removed."""
    parts: list[str] = []
    for path in sorted((store.trace_dir / "observation_trace").glob("observation_trace_*.txt")):
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return _squeeze(" ".join(parts))


def quoted_only_in_observation(finding: dict, page_text: str, observation_text: str) -> list[str]:
    """The finding's quotes that the persona saw and no page of the session had."""
    if not page_text or not observation_text:
        return []
    text = " ".join(str(finding.get(k) or "") for k in ("evidence", "description", "issue"))
    out = []
    for q in _QUOTED.findall(text):
        q = _TRAILING_ELLIPSIS_RE.sub("", q.strip()).strip(" .,:;")
        # A URL or path is not page text; the site shows it in the address bar.
        if len(q) < 4 or q.startswith(("/", "http", "www.")):
            continue
        key = _squeeze(q)
        if key not in page_text and key in observation_text and q not in out:
            out.append(q)
    return out


# Quoted element names inside a finding's evidence — 'Następny krok',
# "Pokaż 10 produktów". Distinctive enough to match the same claim restated in
# free text, short enough not to need fuzzy matching.
_QUOTED_RE = re.compile(r"['‘’\"„”]([^'‘’\"„”]{4,60})['‘’\"„”]")


# Words too common to carry a topic. Kept short on purpose: the threshold below
# does the real work, and a long list would quietly stop matching real pairs.
_STOPWORDS = frozenset("""
przez które który która żeby jest są była było były został została zostało
oraz także jednak ponieważ dlatego bardzo tylko nawet wszystkie każdy
strona stronie strony użytkownik użytkownika użytkownikowi system systemu
this that with from have been were there their which what when
""".split())

# How many distinctive stems two texts must share before one counts as a
# restatement of the other. Four is high enough that two genuinely different
# complaints about the same page do not collide, and low enough to catch a
# one-sentence blocker summarising a three-sentence finding. Measured over the
# archived corpus before being chosen.
_RESTATEMENT_STEMS = 4


def _content_stems(text: str) -> set:
    """Distinctive word stems, for comparing what two texts are *about*."""
    words = re.findall(r"[a-ząćęłńóśźż]{5,}", (text or "").lower())
    return {w[:6] for w in words if w not in _STOPWORDS}


def restates_a_gated_finding(texts, findings) -> bool:
    """Does this free text repeat a claim the gate already threw out?

    Gating protects the findings list. It does not protect the persona's own
    reflection, which is written from the same session and often says the same
    thing in the first person — and which the report renders directly under
    "Co przeszkadzało" and "Najważniejsza poprawka". Both re-run reports carried
    a suppressed claim to the client this way: hyundai's "'Następny krok' był
    nieaktywny" (gated unsupported) and lidl's filter-modal failure (gated
    harness_suspect) were asserted as site defects in the narrative.

    Matching is on the element name the finding quotes, because that is the part
    a restatement keeps. Two independent claims about the same button are rare;
    a restatement of the same one is not.
    """
    from .severity import excluded_from_report

    blob = " ".join(str(t) for t in (texts or []) if t).lower()
    if not blob:
        return False
    for f in findings or []:
        if not isinstance(f, dict) or not excluded_from_report(f):
            continue
        evidence = f"{f.get('evidence', '')} {f.get('recommendation', '')}"

        # 0. An unresponsive-control finding restated: the same "ignored my
        # choice" claim about the same control. The hyundai reflection wrote
        # "system zignorował wybór droższej wersji" without quoting the full
        # "EXECUTIVE MY27", so the quoted-name match below missed it — and that
        # persona's "failed" verdict rested entirely on the held-back claim.
        control = str(f.get("unresponsive_control") or "")
        if control:
            first = next((w for w in control.split("_") if len(w) >= 5 and not w.isdigit()), "")
            if (first and first in _norm_label(blob)
                    and any(m in blob for m in _IGNORED_CHOICE_MARKERS)):
                return True

        # 1. The element name, when the finding quotes one.
        for quoted in _QUOTED_RE.findall(evidence):
            token = quoted.strip().lower()
            if len(token) < 4 or (" " not in token and len(token) < 6):
                continue
            if token in blob:
                return True

        # A prose restatement with nothing quoted on either side — "formularzem
        # zapisu na jazdę próbną" in the finding, "Niedziałający formularz
        # zapisu na jazdę próbną" in the blocker — is NOT caught here, and
        # deliberately so. Word-overlap was tried: at every threshold that
        # caught the known case it also flagged most of the archived corpus,
        # and without ground truth there is no way to tell restatement from two
        # honest complaints about the same page. Shipping a tuned guess would
        # suppress real findings invisibly, which is worse than the leak.
        #
        # The leak is closed structurally instead: reporter_html labels the
        # whole "Realizacja zadania" section as participants' own accounts,
        # which is true of every reflection whether or not it restates
        # anything. See _render_objectives.
    return False


# "The site ignored my choice." The claim a failed click produces when the
# participant's own click never registered.
#
# The hyundai run's headline critical finding: "system ignoruje wybór
# użytkownika dotyczący wersji wyposażenia", cited at the summary page.
# EXECUTIVE was clicked in all three sessions, six clicks in all, and none of
# them registered: SMART stayed ticked in the screenshot straight after each
# one. The summary then showed SMART — correctly, since nothing else had been
# chosen. So the claim that the site ignored a choice is false whatever the
# cause. Whether the card would respond to a person is a separate, open
# question a trace cannot settle; that is exactly what requires_human_check is
# for, and the finding goes to the report's verification list, not away.
_IGNORED_CHOICE_MARKERS = (
    "zignorow", "ignoruj", "nie zapisał", "nie zapisuje", "nie zapisano",
    "nie uwzględni", "nie zarejestrow", "nie został wybran", "nie została wybran",
    "nie zostało wybran", "wybór nie", "ignored", "did not register",
    "was not selected", "not saved",
)


def _norm_label(text: str) -> str:
    """Lowercase, letters and digits only — non-ASCII dropped, not transliterated.

    The parser mangles Polish letters out of its ids ("Następny krok" becomes
    "nast_pny_krok"), so the same must happen to prose for the two to compare.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _retried_controls(store: TraceStore) -> dict[str, str]:
    """Controls clicked more than once this session, keyed for matching prose.

    Keyed on the first two words of the parser id — "executive_my27_od_14"
    becomes "executivemy27" — because the id's tail is the card's price text,
    which the finding never repeats.
    """
    counts: dict[str, int] = {}
    for raw in store.read_action_trace() or []:
        a = _parse_action(raw)
        if str(a.get("action", "")).lower() != "click" or not a.get("target"):
            continue
        counts[str(a["target"])] = counts.get(str(a["target"]), 0) + 1
    keyed: dict[str, str] = {}
    for target, n in counts.items():
        if n < 2:
            continue
        words = [w for w in target.split("_") if w and not w.isdigit()]
        key = _norm_label("".join(words[:2]))
        if len(key) >= 6:
            keyed[key] = target
    return keyed


def _unresponsive_control(finding: dict, retried: dict[str, str]) -> str | None:
    text = f"{finding.get('evidence', '')} {finding.get('recommendation', '')}"
    low = text.lower()
    if not any(m in low for m in _IGNORED_CHOICE_MARKERS):
        return None
    flat = _norm_label(text)
    return next((target for key, target in retried.items() if key in flat), None)


def _cause_of(f: dict, causes: dict, suspected: bool, control, misquoted) -> dict | None:
    """{"code", "detail", "step"} for why a harness-suspect finding is suspect."""
    try:
        step = int(f.get("step"))
    except (TypeError, ValueError):
        step = None
    if step is not None and step in causes:
        code, detail = causes[step]
        return {"code": code, "detail": detail, "step": step}
    if control is not None:
        return {"code": "unresponsive_control", "detail": "", "step": step}
    if misquoted:
        return {"code": "quoted_not_on_page", "detail": "", "step": step}
    # Not the cited step: the finding's words name an element stuck elsewhere,
    # or its claim is contradicted by the session (search ran, content was lost).
    return {"code": "claim_contradicted", "detail": "", "step": step}


def flag_harness_suspects(store: TraceStore, findings: list) -> list:
    """Stamp each finding with `harness_suspect` / `requires_human_check` when it
    is attributable to a harness interaction failure — either its cited step
    lands on one, or its evidence text names an element that was stuck in one.
    Returns the same list (findings mutated in place); non-dicts pass through."""
    causes = harness_suspect_causes(store)
    suspect = set(causes)
    # Non-descriptive accessible names the pages had ("Pasek zadań", "Flyout"):
    # the accessibility section reports them from the snapshots, so a held
    # remark that names one can say where the substance is reported.
    from .a11y_checks import generic_names_in_session
    generic_names = generic_names_in_session(store)
    targets = _suspect_targets(store, suspect)
    # Steps where our own simplifier dropped the page's content. Only findings
    # that claim the content was missing are affected by it.
    perception_lost = perception_loss_steps(store)
    # If the search demonstrably ran, any "search is broken" claim is
    # contradicted by the agent's own navigation.
    search_contradicted = _search_ran(store)
    retried = _retried_controls(store)
    page_text = session_page_text(store)
    observation_text = session_observation_text(store)
    # Repeated clicks that changed the page every time. Still held, but a
    # finding held only because of them says so instead of blaming the tool.
    progress = progress_run_steps(store)
    if progress:
        strict = harness_suspect_steps(store, progress_runs=False)
        strict_targets = _suspect_targets(store, strict)

    def _is_suspect(f: dict, suspect=suspect, targets=targets) -> bool:
        step = f.get("step")
        try:
            step = int(step)
        except (TypeError, ValueError):
            step = None
        if step is not None and step in suspect:
            return True
        text = f"{f.get('evidence', '')} {f.get('recommendation', '')}"
        if _names_target(text, targets):
            return True
        if search_contradicted and _claims_search_broken(f):
            return True
        # "The products are not displayed" cited against a step whose raw page
        # was full of prices: the browser had them, the agent did not.
        if step is not None and step in perception_lost and _claims_content_absent(f):
            return True
        return False

    # Idempotent: always write both outcomes, so re-running after a rule change
    # clears stale flags instead of leaving them latched on.
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        control = _unresponsive_control(f, retried)
        misquoted = quoted_only_in_observation(f, page_text, observation_text)
        suspected = _is_suspect(f) or control is not None or bool(misquoted)
        # Named so the report can say exactly what a person should click.
        f["unresponsive_control"] = control
        # And which quoted words to look for on the page.
        f["quoted_not_on_page"] = misquoted
        f["harness_suspect"] = suspected
        text_low = f"{f.get('evidence', '')} {f.get('recommendation', '')}".lower()
        f["named_in_accessibility"] = [
            generic_names[n] for n in sorted(generic_names)
            if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text_low)] or None
        # Why, as far as the trace says: the signature that marked the cited
        # step, or what matched the finding's text. None when it is not suspect.
        f["harness_cause"] = _cause_of(f, causes, suspected, control, misquoted) if suspected else None
        # The control whose repeated clicks worked, when that is the only reason.
        f["repeated_with_progress"] = None
        if suspected and progress and control is None and not misquoted \
                and not _is_suspect(f, strict, strict_targets):
            try:
                f["repeated_with_progress"] = progress.get(int(f.get("step")))
            except (TypeError, ValueError):
                pass
            if not f["repeated_with_progress"]:
                text = f"{f.get('evidence', '')} {f.get('recommendation', '')}"
                f["repeated_with_progress"] = next(
                    (t for t in set(progress.values()) if t.lower() in text.lower()), None)
        # Two keys, not one, and they are not synonyms even though this writer
        # sets both from the same bool:
        #   harness_suspect      — we caused it, so it is probably not a defect.
        #   requires_human_check — nobody can settle it from the trace alone.
        # Everything harness-suspect needs a human, hence the same value here,
        # but the converse does not hold: viewport_scan flags drag affordances
        # requires_human_check without any harness failure. That is why
        # severity.severity_score ORs the two rather than reading one.
        f["requires_human_check"] = suspected
    return findings
