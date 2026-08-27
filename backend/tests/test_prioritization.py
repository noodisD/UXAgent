"""Severity x impact x confidence prioritization (METHODOLOGY_PLAN §6.2).

Impact is *derived*, never asked for. The aggregate prompt already emits an
`impact` field, but it is a paragraph of prose — you cannot sort by it. Asking
the model for low/medium/high instead would be the fourth self-report in this
codebase to collapse: first_impression returned the prompt's example scores 71%
of the time, confidence said "high" on 56 of 59 findings, and accessibility
returned the example's 3 on 62% of scans.

The trace already answers it. Impact = reach x task consequence:
  reach    — how many of the run's personas reported the finding (counted)
  blocked  — whether any persona reporting it failed or abandoned their task

Both components ship with the label so a reader can disagree with the rule
rather than take "high" on faith.
"""
from src.simulated_web_agent.main.prioritization import derive_impact, prioritize


def _f(hid=1, severity="poważny", persona="p1", confidence="high", step=0):
    return {"heuristic_id": hid, "heuristic_name": f"H{hid}", "severity": severity,
            "confidence": confidence, "step": step, "_persona_dir": persona,
            "_persona_name": persona, "verified": True, "harness_suspect": False}


# --- impact derivation ---------------------------------------------------

def test_widespread_and_blocking_is_high():
    assert derive_impact(personas_affected=3, participant_count=3, blocked=True) == "high"


def test_widespread_but_nobody_failed_is_medium():
    assert derive_impact(personas_affected=3, participant_count=3, blocked=False) == "medium"


def test_isolated_but_blocking_is_medium():
    """One persona hit it and could not finish — narrow reach, real cost."""
    assert derive_impact(personas_affected=1, participant_count=5, blocked=True) == "medium"


def test_isolated_and_harmless_is_low():
    assert derive_impact(personas_affected=1, participant_count=5, blocked=False) == "low"


def test_half_the_panel_counts_as_widespread():
    assert derive_impact(personas_affected=2, participant_count=4, blocked=False) == "medium"


def test_one_person_is_never_widespread_however_small_the_panel():
    """With 2 personas, one is 50% — by ratio alone every finding is
    "widespread" and the whole matrix comes back high. That is what run
    2026-07-16_13-41-14 produced: 5 of 5 rows high, one of them a step-4-of-32
    finding. Reach means more than one person, always."""
    assert derive_impact(personas_affected=1, participant_count=2, blocked=False) == "low"
    # ...and blocking alone must not promote it past medium.
    assert derive_impact(personas_affected=1, participant_count=2, blocked=True) == "medium"
    # Both personas really did hit it -> that is reach.
    assert derive_impact(personas_affected=2, participant_count=2, blocked=True) == "high"


def test_no_participants_does_not_crash():
    assert derive_impact(personas_affected=0, participant_count=0, blocked=False) == "low"


# --- the matrix ----------------------------------------------------------

def test_rows_carry_the_evidence_for_their_impact():
    """A reader must be able to check the rule, not just trust the label."""
    rows = prioritize([_f(hid=1, persona="p1"), _f(hid=1, persona="p2")],
                      {"p1": "failed", "p2": "success"})
    r = rows[0]
    assert r["personas_affected"] == 2
    assert r["participant_count"] == 2
    assert r["blocked"] is True
    assert r["blocked_personas"] == ["p1"]
    assert r["impact"] == "high"


def test_one_row_per_heuristic_not_per_finding():
    rows = prioritize([_f(hid=2, persona="p1"), _f(hid=2, persona="p2"), _f(hid=5, persona="p1")],
                      {"p1": "success", "p2": "success"})
    assert sorted(r["heuristic_id"] for r in rows) == [2, 5]


def test_worst_severity_in_the_group_wins():
    rows = prioritize([_f(hid=1, severity="drobny", persona="p1"),
                       _f(hid=1, severity="krytyczny", persona="p2")],
                      {"p1": "success", "p2": "success"})
    assert rows[0]["severity"] == "krytyczny"


def test_ordered_by_severity_then_impact_then_confidence():
    findings = [
        _f(hid=1, severity="drobny", persona="p1"),                       # low sev
        _f(hid=2, severity="krytyczny", persona="p1"),                    # top
        _f(hid=3, severity="poważny", persona="p1", confidence="low"),
        _f(hid=4, severity="poważny", persona="p1", confidence="high"),
    ]
    rows = prioritize(findings, {"p1": "success"})
    assert [r["heuristic_id"] for r in rows] == [2, 4, 3, 1]
    assert [r["priority"] for r in rows] == [1, 2, 3, 4]


def test_abandonment_counts_as_blocked():
    """A persona who gave up did not finish, whatever the outcome label says."""
    rows = prioritize([_f(persona="p1")], {"p1": "persona_abandoned"})
    assert rows[0]["blocked"] is True


def test_harness_suspects_never_reach_the_matrix():
    """They are not confirmed defects — prioritising them would rank a harness
    bug against real findings."""
    f = _f(persona="p1")
    f["harness_suspect"] = True
    assert prioritize([f], {"p1": "failed"}) == []


def test_empty_input_is_an_empty_matrix():
    assert prioritize([], {}) == []
