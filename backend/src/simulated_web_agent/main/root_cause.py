"""Root-cause signatures — collapse the same underlying site defect when the
heuristic-evaluation pass files it under multiple different Nielsen heuristics.

Ground truth (20-persona dm.pl run, 2026-07-30): the search box accepted a new
query but the URL/results silently stayed on the old one. Six personas hit it
independently, in six different contexts, and the LLM filed it as SIX separate
"krytyczny" findings under six different heuristics (H1 status visibility, H2
real-world match, H3 user control, H4 consistency, H5 error prevention, H9
error recovery) — inflating the headline critical count 6x for one bug, which
is very likely what anchored the aggregate score near the floor.

Deliberately NOT a text-similarity/embedding clusterer: that risks the worse
failure of merging two genuinely DIFFERENT bugs into one, silently hiding a
real defect. Instead, a small library of NAMED, evidence-grounded marker
signatures — precision over recall, the same idiom as attribution.py's
_FALLBACK_MARKERS / _claims_search_broken. A finding is only clustered when it
matches a signature with high confidence (both a query-context marker AND a
stale-result marker); everything else is left alone.
"""
from __future__ import annotations

from .session_goal_state import has_marker

# --- search_stale_results ----------------------------------------------------
# Both the query context (it's about search) and the stale/no-update symptom
# must be present. Marker text is drawn verbatim from the 6 real findings that
# motivated this signature (Aleksy/Piotr/Ola/Kasia/Katarzyna/Alex/Małgorzata,
# H1/H2/H3/H4/H5/H9 of the above-cited run).
_QUERY_CONTEXT_MARKERS = ("zapytani", "wyszukiw", "hasł")
_STALE_RESULT_MARKERS = (
    "nie aktualizuje", "nie zaktualizował", "nie zaktualizowała", "nie zaktualizowały",
    "nie odświeżyła", "nie odświeżył", "nie odświeżyło",
    "nie zareagował", "nie zareagowała",
    "nie przeniosła", "nie przeniósł",
    "poprzedniego zapytania",
    "nadal wyświetlał", "nadal wyświetlała",
    "wciąż wyświetlał", "wciąż wyświetlała",
    "nie wyświetlił listy wyników", "nie wyświetliła listy wyników",
)

# --- financing_info_missing --------------------------------------------------
# The same absence reported under two heuristics in the 2026-09-30 hyundai run:
# H2 "brak prezentacji estymowanej raty miesięcznej" and H7 "brak kalkulatora
# rat", by two personas on the summary page. Two issues, two score deductions,
# one missing thing. Both a financing context AND an absence symptom are needed,
# so "the rate calculator does not work" is left alone. Run over the 64
# recorded findings it matches none of them, and both of those.
_FINANCING_CONTEXT_MARKERS = (
    "raty miesięcz", "ratę miesięcz", "rat miesięcz", "kalkulator rat", "kalkulatora rat",
    "kalkulator finans", "kalkulatora finans", "symulator finans", "symulatora finans",
    "raty leasing", "rat leasing",
)
_ABSENCE_MARKERS = (
    "brak", "nie podaje", "nie prezentuje", "nie ma ", "bez prezentacji", "jedynie całkow",
    "tylko całkow", "nie wyświetla", "nie pokazuje",
)

# (root_cause_id, Polish display name, context markers, symptom markers)
_SIGNATURES = (
    ("search_stale_results",
     "Wyszukiwarka nie aktualizuje wyników dla nowego zapytania",
     _QUERY_CONTEXT_MARKERS, _STALE_RESULT_MARKERS),
    ("financing_info_missing",
     "Brak raty miesięcznej lub kalkulatora finansowania w podsumowaniu",
     _FINANCING_CONTEXT_MARKERS, _ABSENCE_MARKERS),
)


def _matches(text: str, context_markers: tuple, symptom_markers: tuple) -> bool:
    return has_marker(text, context_markers) and has_marker(text, symptom_markers)


def assign_root_causes(findings: list) -> list:
    """Stamp `root_cause_id`/`root_cause_name` on findings matching a known
    signature (both None if none match). Idempotent — clears stale tags first,
    same contract as flag_off_site/cap_confidence. Mutates and returns the same
    list; findings sharing a `root_cause_id` are the same underlying defect
    regardless of which heuristic_id they were filed under."""
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        f.pop("root_cause_id", None)
        f.pop("root_cause_name", None)
        text = f"{f.get('evidence', '')} {f.get('recommendation', '')}".lower()
        for rc_id, rc_name, ctx_markers, symptom_markers in _SIGNATURES:
            if _matches(text, ctx_markers, symptom_markers):
                f["root_cause_id"] = rc_id
                f["root_cause_name"] = rc_name
                break
    return findings
