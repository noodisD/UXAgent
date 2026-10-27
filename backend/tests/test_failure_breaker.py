"""The failed-action circuit breaker blocks a hopeless retry, not a fresh attempt.

It was one session-wide Counter keyed by the whole action:
- Kacper's store-locator `type` failed twice, and every later return to that page
  (a fresh page) had its first `type` replaced by a forced back;
- `type enter=false` and `type enter=true` were different keys, so the breaker
  never fired for Julia;
- the thought it wrote called the element broken "a UX finding worth noting",
  which reached surveys and reflections as a defect of the site.
"""
import json
from pathlib import Path

from simulated_web_agent.main import attribution
from simulated_web_agent.main.loop_guards import (
    FAILED_THOUGHT,
    FailureBreaker,
    action_key,
    current_url,
    failure_key,
    visit_over,
)

LOCATOR = "https://x.pl/sklepy"
CART = "https://x.pl/koszyk"
ERROR = "Error executing action: Timeout 30000ms exceeded."
INPUT_REFUSED = "Input blocked: this would enter personal data (email)."
SCOPE_REFUSED = "Navigation blocked: https://other.com/ is outside the site under test."


def _act(name, description="opis", **fields):
    return json.dumps({"action": name, "description": description, **fields})


def _type(target, text="Warszawa", enter=False):
    return _act("type", target=target, text=text, enter=enter)


def _click(target):
    return _act("click", target=target)


def _fail(breaker, action, url, error=ERROR):
    breaker.record(action, error, url)


def test_two_failures_block_the_third_on_one_visit():
    b = FailureBreaker()
    click = _click("filtr")
    assert b.verdict(click, LOCATOR) is None
    _fail(b, click, LOCATOR)
    assert b.verdict(click, LOCATOR) is None
    _fail(b, click, LOCATOR)
    assert b.verdict(click, LOCATOR) == "failed"
    assert b.attempts(click, LOCATOR) == 2


def test_enter_flag_and_text_are_the_same_attempt():
    """Julia: type enter=False, then True, then True again."""
    b = FailureBreaker()
    first = _type("miasto", "Warszawa", enter=False)
    second = _type("miasto", "Warszawa", enter=True)
    third = _type("miasto", "Warszawa", enter=True)
    assert action_key(first) == action_key(second)
    _fail(b, first, LOCATOR)
    _fail(b, second, LOCATOR)
    assert b.verdict(third, LOCATOR) == "failed"
    # Other text into the same field is the same failed attempt as well.
    assert b.verdict(_type("miasto", "Kraków", enter=True), LOCATOR) == "failed"


def test_leaving_the_page_gives_a_fresh_two_tries():
    """Kacper: back from the store locator, then return to it."""
    b = FailureBreaker()
    typed = _type("miasto")
    _fail(b, typed, LOCATOR)
    _fail(b, typed, LOCATOR)
    assert b.verdict(typed, LOCATOR) == "failed"
    b.page_changed()
    assert b.verdict(typed, LOCATOR) is None
    _fail(b, typed, LOCATOR)
    assert b.verdict(typed, LOCATOR) is None
    _fail(b, typed, LOCATOR)
    assert b.verdict(typed, LOCATOR) == "failed"


def test_a_failure_whose_own_action_changed_the_page_is_not_carried_over():
    b = FailureBreaker()
    click = _click("filtr")
    _fail(b, click, LOCATOR)
    b.page_changed()  # what the loop does once the step has landed elsewhere
    assert b.verdict(click, CART) is None
    _fail(b, click, CART)
    assert b.verdict(click, CART) is None


def test_a_success_clears_the_count():
    b = FailureBreaker()
    click = _click("filtr")
    _fail(b, click, LOCATOR)
    b.record(click, None, LOCATOR)
    _fail(b, click, LOCATOR)
    assert b.verdict(click, LOCATOR) is None
    # The per-page total is cleared too: five fail/succeed pairs never add up.
    for _ in range(5):
        b.record(click, None, LOCATOR)
        _fail(b, click, LOCATOR)
        assert b.verdict(click, LOCATOR) is None


def test_four_failures_on_one_page_block_for_the_session():
    b = FailureBreaker()
    typed = _type("miasto")
    for _ in range(2):
        _fail(b, typed, LOCATOR)
    b.page_changed()
    for _ in range(2):
        _fail(b, typed, LOCATOR)
    b.page_changed()
    # A new visit, nothing failed on it, and still blocked: four is the budget.
    assert b.visit[failure_key(typed)] == 0
    assert b.verdict(typed, LOCATOR) == "failed"


def test_same_control_id_failing_on_another_page_is_not_blocked():
    b = FailureBreaker()
    add = _click("dodaj_do_koszyka")
    for _ in range(2):
        _fail(b, add, LOCATOR)
    b.page_changed()
    for _ in range(2):
        _fail(b, add, LOCATOR)
    b.page_changed()
    assert b.verdict(add, LOCATOR) == "failed"
    assert b.verdict(add, CART) is None
    _fail(b, add, CART)
    assert b.verdict(add, CART) is None


def test_a_single_page_apps_route_is_a_page_of_its_own():
    b = FailureBreaker()
    colours = "https://x.pl/konfigurator#/colours"
    summary = "https://x.pl/konfigurator#/summary"
    nxt = _click("dalej")
    for _ in range(2):
        _fail(b, nxt, colours)
    b.page_changed()
    for _ in range(2):
        _fail(b, nxt, colours)
    b.page_changed()
    assert b.verdict(nxt, colours) == "failed"
    assert b.verdict(nxt, summary) is None


def test_refusals_stay_text_specific():
    b = FailureBreaker()
    email = _type("email", "jan@x.pl")
    _fail(b, email, LOCATOR, INPUT_REFUSED)
    assert b.verdict(email, LOCATOR) is None
    _fail(b, email, LOCATOR, INPUT_REFUSED)
    assert b.verdict(email, LOCATOR) == "refused"
    assert b.verdict(_type("email", "jan@x.pl", enter=True), LOCATOR) == "refused"
    # Other text into the field that refused one is not blocked, and a refusal
    # is not a tool failure: it must not feed the failed-attempt counts.
    assert b.verdict(_type("email", "Warszawa"), LOCATOR) is None
    assert not b.visit and not b.total


def test_an_off_site_address_refused_twice_is_refused():
    b = FailureBreaker()
    goto = _act("goto_url", url="https://other.com/sklep")
    for _ in range(2):
        _fail(b, goto, LOCATOR, SCOPE_REFUSED)
    assert b.verdict(goto, LOCATOR) == "refused"
    assert b.verdict(_act("goto_url", url="https://x.pl/sklep"), LOCATOR) is None


def test_failure_key_is_the_target_not_the_payload():
    assert failure_key(_type("a", "x", enter=True)) == failure_key(_type("a", "y"))
    assert failure_key(_type("a")) != failure_key(_type("b"))
    assert failure_key(_click("a")) != failure_key(_type("a"))
    assert failure_key(_act("select", target="s", value="1")) == failure_key(
        _act("select", target="s", value="2"))
    assert failure_key(_act("goto_url", url="https://x.pl/a?q=1")) == failure_key(
        _act("goto_url", url="https://x.pl/a?q=2"))
    assert failure_key(_act("goto_url", url="https://x.pl/a")) != failure_key(
        _act("goto_url", url="https://x.pl/b"))
    enter = _act("key_press", key="Enter", target="q")
    assert failure_key(enter) != failure_key(_act("key_press", key="Tab", target="q"))
    assert failure_key(enter) != failure_key(_act("key_press", key="Enter", target="r"))
    # Everything else keeps its whole identity.
    assert failure_key(_act("scroll", direction="down", amount=2)) != failure_key(
        _act("scroll", direction="down", amount=3))


def test_action_key_ignores_the_description_and_survives_odd_input():
    assert action_key(_click("a")) == action_key(_act("click", description="inaczej", target="a"))
    assert action_key(json.dumps([{"action": "click", "target": "a"}])) == action_key(_click("a"))
    assert action_key("not json") == "not json"
    assert failure_key("not json") == "not json"


def test_current_url_prefers_the_active_tab():
    tabs = [{"url": "https://x.pl/a", "is_active": False},
            {"url": "https://x.pl/b", "is_active": True}]
    assert current_url({"tabs": tabs}) == "https://x.pl/b"
    assert current_url({"tabs": [{"url": "https://x.pl/a"}, {"url": "https://x.pl/b"}]}) == "https://x.pl/a"
    assert current_url({"tabs": []}) == "" and current_url({}) == ""


def test_failed_thought_never_says_broken_or_ux_finding():
    thought = FAILED_THOUGHT.format(tries=2, what="Klikam filtr")
    lowered = thought.lower()
    assert "broken" not in lowered and "ux finding" not in lowered
    assert "cannot tell whether the site" in thought
    assert "Klikam filtr" in thought and "2 times" in thought
    assert not any(m in lowered for m in attribution._FALLBACK_MARKERS)


def test_experiment_records_the_failure_before_it_clears_the_visit():
    """Drift guard: cleared first, a failing action that changed the page would
    leave its count behind for the next page."""
    src = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
           / "main" / "experiment.py").read_text(encoding="utf-8")
    assert src.index("breaker.record(") < src.index("breaker.page_changed()")
    assert "visit_over(" in src
    assert "fail_counts" not in src and "refused_keys" not in src


def test_a_dead_address_is_blocked_from_every_page():
    """The old breaker stopped a failing goto_url for the whole session; counting
    it per page would give the same dead address two more tries from each page."""
    b = FailureBreaker()
    goto = _act("goto_url", url="https://x.pl/nie-ma")
    _fail(b, goto, LOCATOR)
    b.page_changed()
    _fail(b, goto, CART)
    assert b.verdict(goto, "https://x.pl/inna") == "failed"
    assert b.attempts(goto, "https://x.pl/inna") == 2


def test_a_different_address_is_not_blocked_by_a_dead_one():
    b = FailureBreaker()
    for _ in range(2):
        _fail(b, _act("goto_url", url="https://x.pl/nie-ma"), LOCATOR)
    assert b.verdict(_act("goto_url", url="https://x.pl/jest"), LOCATOR) is None


def test_raw_type_text_variants_are_the_same_attempt():
    b = FailureBreaker()
    _fail(b, _act("raw_type", text="Warszawa"), LOCATOR)
    _fail(b, _act("raw_type", text="Warsaw"), LOCATOR)
    assert b.verdict(_act("raw_type", text="00-001"), LOCATOR) == "failed"


def test_an_unhashable_action_name_does_not_crash_the_loop():
    odd = json.dumps({"action": ["click"], "target": "x"})
    b = FailureBreaker()
    assert isinstance(failure_key(odd), str)
    assert b.verdict(odd, LOCATOR) is None


def test_the_visit_ends_when_the_page_content_moves_on_at_the_same_address():
    """A single-page app's step 2 is a page of its own although the URL is the same."""
    assert visit_over(LOCATOR, LOCATOR, "<step 1>", "<step 2>", None) is True


def test_a_failing_action_that_left_the_page_alone_does_not_end_the_visit():
    assert visit_over(LOCATOR, LOCATOR, "<step 1>", "<step 1>", ERROR) is False
    # Failed halfway: content moved but the action still errored.
    assert visit_over(LOCATOR, LOCATOR, "<step 1>", "<step 1 half>", ERROR) is False


def test_a_new_address_ends_the_visit_whatever_the_outcome():
    assert visit_over(LOCATOR, CART, "<a>", "<a>", ERROR) is True
    assert visit_over(LOCATOR, CART, "<a>", "<b>", None) is True
