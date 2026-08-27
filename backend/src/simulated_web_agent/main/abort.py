"""Persona abort thresholds (§2.1 "Reguły zachowania" / §2.2).

Real users drop off when a flow stops making sense; the agent should too,
instead of grinding every step into a wall. A persona's patience (or an explicit
max-confusing-steps) determines how many consecutive no-progress steps it
tolerates before abandoning — turning drop-off into a first-class UX signal
rather than a maxed-out step budget.
"""

_PATIENCE_STEPS = {"low": 3, "medium": 5, "high": 8}
_DEFAULT = 4


def abandon_threshold(persona_card: dict | None) -> int:
    """Consecutive no-progress steps this persona tolerates before abandoning."""
    card = persona_card or {}
    explicit = card.get("abort_max_confusing_steps")
    try:
        n = int(explicit)
        if n > 0:
            return n
    except (TypeError, ValueError):
        pass
    patience = str((card.get("behavioral_profile") or {}).get("patience", "")).lower()
    return _PATIENCE_STEPS.get(patience, _DEFAULT)
