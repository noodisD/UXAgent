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
import logging
import re

from .trace_store import TraceStore, parse_action as _parse_action

log = logging.getLogger(__name__)

# Substrings of the harness fallback descriptions emitted when an element/method
# "didn't work after several tries" (experiment.py). Their presence at a step is
# direct proof the interaction failed on the harness side, not the site side.
_FALLBACK_MARKERS = ("nie działał", "nie działała", "wybieram inną", "innej drogi")

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


def harness_suspect_steps(store: TraceStore) -> set[int]:
    """Step indices whose action is a harness interaction failure.

    Four signatures, all deterministic:
      1. a fallback action ("...nie działał mimo kilku prób...") — and the step
         it retreated from, which is where the finding is usually anchored;
      2. an (action, target) pair repeated >= _REPEAT_THRESHOLD times — a stuck
         loop where the agent retried because the interaction had no effect;
      3. a near-empty observation over a large raw page — a perception-capture
         artifact ("The page is empty" while raw_html is tens of KB), i.e. the
         perceiver failed, not the site;
      4. the executor reported it could not perform the action at all;
      5. a consent notice was on screen and never dismissed;
      6. the page was a bot-detection challenge rather than the site.
    """
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    suspect: set[int] = set()

    # (4) Executor failures. observations[i] is the state recorded *before*
    # action i (experiment.py appends at the top of the loop), so the error it
    # carries was produced by action i-1 — that is the step to blame. Step i is
    # added too: the agent perceives the un-changed page there, and that is
    # usually where the resulting finding anchors.
    #
    # This is the strongest harness-vs-site signal in the run and it was written
    # to disk every session while nothing read it. It is what turned pepco.pl's
    # consent overlay into "critical site defects".
    for i, obs in enumerate(store.read_observations() or []):
        if not isinstance(obs, dict):
            continue
        err = str(obs.get("error") or "").lower()
        if any(m in err for m in _EXECUTOR_ERROR_MARKERS):
            if i - 1 >= 0:
                suspect.add(i - 1)
            suspect.add(i)

    # (5) A consent notice was on screen and would not go away, so every
    # interaction from that point may have been intercepted by the overlay.
    # Nothing downstream could see this before: dismiss_cookie_banners returned
    # a bare bool and recorded nothing.
    try:
        unresolved = store.unresolved_consent_steps()
    except Exception:
        unresolved = set()
    if unresolved:
        n_steps = len(actions)
        for start in sorted(unresolved):
            suspect.update(range(start, n_steps))

    # (6) Bot-detection challenge pages. Anything the auditor concludes from a
    # screen that exists because we are automated is about us, not the site.
    suspect.update(bot_wall_steps(store))

    for i in range(len(actions)):
        obs = store.read_observation_step_text(i).strip()
        if obs and len(obs) <= _ARTIFACT_OBS_MAX_CHARS:
            if store.raw_html_size(i) >= _ARTIFACT_RAW_MIN_BYTES:
                suspect.add(i)

    for i, a in enumerate(actions):
        desc = (a.get("description") or "").lower()
        if any(m in desc for m in _FALLBACK_MARKERS):
            suspect.add(i)
            if i - 1 >= 0:
                suspect.add(i - 1)

    # Consecutive runs of the same (action, target).
    run_start = 0
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
        run_len = i - run_start
        if run_len >= _REPEAT_THRESHOLD and prev.get("target"):
            suspect.update(range(run_start, i))
        run_start = i

    return suspect


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


def flag_harness_suspects(store: TraceStore, findings: list) -> list:
    """Stamp each finding with `harness_suspect` / `requires_human_check` when it
    is attributable to a harness interaction failure — either its cited step
    lands on one, or its evidence text names an element that was stuck in one.
    Returns the same list (findings mutated in place); non-dicts pass through."""
    suspect = harness_suspect_steps(store)
    targets = _suspect_targets(store, suspect)
    # Steps where our own simplifier dropped the page's content. Only findings
    # that claim the content was missing are affected by it.
    perception_lost = perception_loss_steps(store)
    # If the search demonstrably ran, any "search is broken" claim is
    # contradicted by the agent's own navigation.
    search_contradicted = _search_ran(store)
    retried = _retried_controls(store)

    def _is_suspect(f: dict) -> bool:
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
        suspected = _is_suspect(f) or control is not None
        # Named so the report can say exactly what a person should click.
        f["unresponsive_control"] = control
        f["harness_suspect"] = suspected
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
