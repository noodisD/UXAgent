"""The report counts issues in one unit: confirmed groups, as the cards do.

severity_counts counts findings, so H1 filed as serious by two personas was
"Serious: 2" in the narrator's prompt — which calls those numbers deduplicated
— and "Łączna liczba problemów: 6 (4 kryt. / 2 poważnych)" in the Markdown
above one serious card.
"""
from simulated_web_agent.main.reporter import _group_counts, _render_aggregated_md

STATS = {"participant_count": 2,
         "heuristic": {"severity_counts": {"krytyczny": 4, "poważny": 2}, "total_findings": 6,
                       "by_heuristic": [{"heuristic_id": h, "verified": True} for h in range(1, 6)]},
         "prioritization": [{"heuristic_id": h, "severity": "krytyczny"} for h in (2, 3, 4, 5)]
                           + [{"heuristic_id": 1, "severity": "poważny"}]}


def test_the_prompt_gets_one_count_per_group():
    assert dict(_group_counts(STATS)) == {"krytyczny": 4, "poważny": 1}


def test_the_markdown_counts_the_issues_it_lists():
    agg = {"website": "x.pl", "statistics": STATS, "narrative": {
        "executive_summary": "S.", "overall_ux_score": 2,
        "critical_issues": [{"heuristic_id": h} for h in (2, 3, 4, 5)],
        "serious_issues": [{"heuristic_id": 1}], "minor_issues": []}}
    md = _render_aggregated_md(agg)
    assert "Łączna liczba problemów: **5** (4 kryt. / 1 poważnych / 0 drobnych)" in md
    assert "z 6 potwierdzonych zgłoszeń uczestników" in md
