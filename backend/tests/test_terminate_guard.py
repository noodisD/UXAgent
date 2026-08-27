"""The action LLM may fall back to `terminate` when a plan step is passive
("review the results...") and nothing in the action space matches. The guard
in Agent._guard_terminate must block that and substitute a scroll, while
letting explicitly-terminal steps through."""

from simulated_web_agent.agent.agent import Agent


class _AgentStub:
    _guard_terminate = Agent._guard_terminate
    _EXPLICIT_STOP = Agent._EXPLICIT_STOP

    def __init__(self, next_step):
        class _Plan:
            pass

        self.current_plan = _Plan()
        self.current_plan.next_step = next_step


def _guard(step, action):
    return _AgentStub(step)._guard_terminate(action)


def test_passive_step_terminate_becomes_scroll():
    result = _guard(
        "Review the search results for promising pillow options, "
        "noting their price and appearance",
        {"action": "terminate", "description": "Reviewing results"},
    )
    assert result["action"] == "scroll"
    assert result["direction"] == "down"
    assert result["description"]  # keeps a description for the live view


def test_explicit_terminate_step_is_allowed():
    result = _guard(
        "Terminate the session, the goal is complete",
        {"action": "terminate", "description": "Done"},
    )
    assert result["action"] == "terminate"


def test_polish_explicit_terminate_is_allowed():
    result = _guard(
        "Zakończ sesję — cel osiągnięty",
        {"action": "terminate", "description": "Koniec"},
    )
    assert result["action"] == "terminate"


def test_conclude_the_task_is_terminal():
    # Real phrasing from run 2026-07-09_10-05-26_be4f, persona 5603.
    result = _guard(
        "Conclude the task, noting that suitable decorative items were "
        "identified within the budget",
        {"action": "terminate", "description": "Concluding the task"},
    )
    assert result["action"] == "terminate"


def test_complete_the_purchase_is_not_terminal():
    result = _guard(
        "Complete the purchase of the selected pillow",
        {"action": "terminate", "description": "Done"},
    )
    assert result["action"] == "scroll"


def test_non_terminate_actions_pass_through_unchanged():
    action = {"action": "click", "target": "produkt_1", "description": "Click"}
    assert _guard("Open the first product", action) is action


def test_missing_plan_blocks_terminate():
    stub = _AgentStub(None)
    stub.current_plan = None
    result = stub._guard_terminate({"action": "terminate", "description": "?"})
    assert result["action"] == "scroll"
