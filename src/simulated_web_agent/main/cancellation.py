"""
Cooperative run cancellation.

POST /runs/<id>/cancel flags a run here. The agent step loop checks the flag
between steps and the pipeline checks it between phases, so a cancelled run
actually stops within one step — instead of the daemon thread continuing to
drive browsers and burn LLM quota until it finishes on its own.
"""
import threading

_lock = threading.Lock()
_cancelled: set[str] = set()


def cancel(run_id: str) -> None:
    with _lock:
        _cancelled.add(run_id)


def is_cancelled(run_id: str | None) -> bool:
    if not run_id:
        return False
    with _lock:
        return run_id in _cancelled


def clear(run_id: str) -> None:
    """Forget a run's flag (called when a new run starts with the same id)."""
    with _lock:
        _cancelled.discard(run_id)
