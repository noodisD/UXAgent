"""Strengths must show who praised what, and what they were looking at.

Karolina asked for the report to mark concretely what works, with screenshots.
The "Mocne strony" section was a bare list of the aggregate model's sentences,
one of them inflated: "brak niedziałających linków na 313 sprawdzonych" was the
same header and footer links summed over every page and every persona. On the
hyundai run the honest figure is 123 (by distinct page); new runs count distinct
links exactly.
"""
from simulated_web_agent.main.reporter import _aggregate_links
from simulated_web_agent.main.reporter_html import _render_strengths


STATS = {
    "first_impression": {
        "strengths": [{"persona": "Krzysztof", "text": "Jasny podział na modele hybrydowe i spalinowe."},
                      {"persona": "Małgorzata", "text": "Czytelny podział na kategorie aut."}],
        "screenshots": [{"persona": "Krzysztof", "screenshot": "p1/screenshot/screenshot_landing.png"}],
    },
    "objective_reflections": [{"persona": "Małgorzata", "wins": ["Łatwe porównanie wersji"]}],
}


def test_each_strength_is_traced_to_a_participant_and_their_screen():
    html = _render_strengths({"key_strengths": ["Czytelny podział oferty"]}, STATS)
    assert "Na czym to się opiera" in html
    assert "Krzysztof" in html and "screenshot_landing.png" in html
    assert "Na ekranie startowym" in html and "W trakcie zadania" in html
    assert "Łatwe porównanie wersji" in html


def test_it_says_these_are_opinions_not_measurement():
    assert "nie pomiar" in _render_strengths({"key_strengths": ["x"]}, STATS)


def test_nothing_to_show_renders_nothing():
    assert _render_strengths({}, {}) == ""


class _Store:
    def __init__(self, findings):
        self._f = findings

    def read_link_findings(self):
        return self._f


def test_links_are_counted_once_however_many_pages_and_personas_repeat_them():
    footer = ["https://x.pl/kontakt", "https://x.pl/regulamin"]
    page = lambda u: {"url": u, "links_checked": 2, "checked_urls": footer, "broken": []}
    stores = [_Store([page("https://x.pl/a"), page("https://x.pl/b")]) for _ in range(3)]
    data, _ = _aggregate_links(stores)
    assert data["total_links_checked"] == 2, "not 12"


def test_older_checks_without_urls_count_each_page_once():
    page = {"url": "https://x.pl/a", "links_checked": 40, "broken": []}
    data, _ = _aggregate_links([_Store([page]), _Store([page]), _Store([page])])
    assert data["total_links_checked"] == 40, "not 120"
