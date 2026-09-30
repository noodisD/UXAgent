"""The snapshot reader counts nodes that Playwright writes with inline text.

A node with a single text child is printed `- button "Name": text`, and the
reader required nothing after the colon, so every such link, button, tab and
text box was left out of the totals. Measured over the 18 recorded runs, the
wider pattern adds only named controls: unnamed counts and image counts do not
move, and the control share shifts by at most 0.3 points."""
from simulated_web_agent.main.a11y_checks import _NODE, parse_aria_snapshot


def test_a_node_with_inline_text_is_a_node():
    for line, role, name in (
        ('  - button "Skonfiguruj swój samochód": Konfigurator', "button", "Skonfiguruj swój samochód"),
        ('- link "Oferta": Zobacz ofertę', "link", "Oferta"),
        ("- textbox: abc", "textbox", None),
        ('- heading "Tytuł" [level=2]: Tytuł strony', "heading", "Tytuł"),
    ):
        m = _NODE.match(line)
        assert m and m.group("role") == role and m.group("name") == name, line


def test_properties_and_text_lines_are_still_not_nodes():
    nodes = parse_aria_snapshot('- text: Model Mandatory Field\n- /url: /pl/pl\n- link "A": x')
    assert [(n.role, n.name) for n in nodes] == [("link", "A")]


def test_an_unnamed_control_with_inline_text_is_counted_unnamed():
    nodes = parse_aria_snapshot('- textbox: abc\n- button "OK": OK')
    assert [(n.role, bool(n.name)) for n in nodes] == [("textbox", False), ("button", True)]
