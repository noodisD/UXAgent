"""
Durable run storage on Google Cloud Storage.

Cloud Run's filesystem is ephemeral: `runs/` is wiped on every restart and
redeploy, so run history existed only for the lifetime of one container. This
module makes finished runs outlive the instance, and makes them visible to
every authenticated user regardless of which instance they hit.

Why sync-at-the-end rather than mounting the bucket at `runs/`
--------------------------------------------------------------
Writing the run directly to a gcsfuse mount would need no code, but the write
pattern is hostile to it:

  * `observation_trace.jsonl` is opened in append mode once per step; on GCS an
    append rewrites the whole object, so the cost grows quadratically.
  * Playwright writes screenshots straight to the path (`page.screenshot(path=)`)
    — ~2.7 MB per full-page shot, on the agent's hot path.
  * A 20-persona run writes ~12,900 files.

So the run is written to local disk exactly as before, and uploaded once it is
complete. The hot path is untouched, and the upload is a single batched pass.

What is stored
--------------
Only what the dashboard reads and what analysis can be rebuilt from — see
_EXCLUDED_DIRS. Dropping the debug artifacts and full-page screenshots takes a
persona from ~202 MB to ~22 MB (90% less) while still supporting the reports,
the evidence thumbnails, and re-running the analysis passes offline. Set
UXAGENT_RUNS_ARCHIVE_ALL=true to keep everything instead.

Layout in the bucket
--------------------
    index.json          — one history row per run, so the history screen costs
                          a single small read instead of hydrating every run
    runs/<run_id>/...   — the durable artifacts, same tree as on disk

Unconfigured (no UXAGENT_RUNS_BUCKET) every function here is a no-op and the
app behaves exactly as it does locally — the same convention UXAGENT_API_KEY
uses, so local development and the test suite need no cloud credentials.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_BUCKET_NAME = os.getenv("UXAGENT_RUNS_BUCKET", "").strip()
_ARCHIVE_ALL = os.getenv("UXAGENT_RUNS_ARCHIVE_ALL", "").strip().lower() in ("1", "true", "yes")

INDEX_BLOB = "index.json"
_RUNS_PREFIX = "runs"

# Debug-only artifacts. No report or dashboard view reads these; they exist for
# post-mortem inspection of a session and dominate the on-disk size.
_EXCLUDED_DIRS = frozenset({"api_trace", "raw_html", "simp_html"})

# Full-page screenshots are a fallback that is never actually served:
# results_api skips them when picking a step's shot, and evidence.py prefers
# the viewport variant. They are 10x the size of the one that gets shown.
def _is_excluded(rel: Path) -> bool:
    if _ARCHIVE_ALL:
        return False
    if any(part in _EXCLUDED_DIRS for part in rel.parts):
        return True
    return rel.name.endswith("_full_page.png")


_client_lock = threading.Lock()
_client: Any = None
_client_failed = False


def is_enabled() -> bool:
    """True when a bucket is configured. Everything else no-ops when False."""
    return bool(_BUCKET_NAME)


def bucket_name() -> str:
    return _BUCKET_NAME


def _bucket() -> Any:
    """Lazily build the GCS client.

    Lazy because importing/authenticating at module import would make every
    test and local run pay for a cloud dependency that is not configured. A
    failure is remembered so a broken deployment logs once, not once per call.
    """
    global _client, _client_failed
    if not is_enabled() or _client_failed:
        return None
    with _client_lock:
        if _client is None:
            try:
                from google.cloud import storage  # imported here, see above

                _client = storage.Client()
            except Exception:
                _client_failed = True
                log.exception(
                    "[storage] cannot initialise GCS client — run history will "
                    "not be durable. Check the bucket and the service account."
                )
                return None
    try:
        return _client.bucket(_BUCKET_NAME)
    except Exception:
        log.exception("[storage] cannot open bucket %s", _BUCKET_NAME)
        return None


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_run(run_dir: Path, history_row: Optional[dict] = None) -> int:
    """Upload a finished run, then merge its row into the shared index.

    Returns the number of files uploaded (0 when disabled). Never raises: a
    storage outage must not turn a completed run into a failed one — the run is
    still on local disk and still served by this instance.
    """
    if not is_enabled():
        return 0
    bucket = _bucket()
    if bucket is None:
        return 0

    run_dir = Path(run_dir)
    run_id = run_dir.name
    uploaded = 0
    skipped_bytes = 0
    try:
        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(run_dir)
            if _is_excluded(rel):
                skipped_bytes += path.stat().st_size
                continue
            blob_name = f"{_RUNS_PREFIX}/{run_id}/{rel.as_posix()}"
            try:
                bucket.blob(blob_name).upload_from_filename(str(path))
                uploaded += 1
            except Exception:
                log.exception("[storage] failed to upload %s", blob_name)
        log.info(
            "[storage] uploaded run %s: %d files (skipped %.0f MB of debug artifacts)",
            run_id, uploaded, skipped_bytes / 1_048_576,
        )
    except Exception:
        log.exception("[storage] upload of run %s failed", run_id)

    if history_row:
        merge_index_row(history_row)
    return uploaded


# ---------------------------------------------------------------------------
# Shared history index
# ---------------------------------------------------------------------------

def read_index() -> list[dict]:
    """History rows for runs held in the bucket ([] when disabled/absent)."""
    if not is_enabled():
        return []
    bucket = _bucket()
    if bucket is None:
        return []
    try:
        blob = bucket.blob(INDEX_BLOB)
        if not blob.exists():
            return []
        rows = json.loads(blob.download_as_bytes().decode("utf-8"))
        return [r for r in rows if isinstance(r, dict)]
    except Exception:
        log.exception("[storage] cannot read %s", INDEX_BLOB)
        return []


def merge_index_row(row: dict) -> None:
    """Insert/replace one run's row in the shared index.

    Read-modify-write. Safe here because the service runs as a single instance
    (--workers 1 / --max-instances 1, since run state is in-memory anyway), and
    a lost index entry is recoverable with rebuild_index().
    """
    if not is_enabled() or not row.get("id"):
        return
    bucket = _bucket()
    if bucket is None:
        return
    try:
        rows = [r for r in read_index() if r.get("id") != row["id"]]
        rows.append(row)
        rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        bucket.blob(INDEX_BLOB).upload_from_string(
            json.dumps(rows, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
    except Exception:
        log.exception("[storage] cannot update %s", INDEX_BLOB)


# ---------------------------------------------------------------------------
# Download / hydrate
# ---------------------------------------------------------------------------

def remote_run_ids() -> list[str]:
    """Run ids present in the bucket."""
    if not is_enabled():
        return []
    bucket = _bucket()
    if bucket is None:
        return []
    try:
        client = bucket.client
        it = client.list_blobs(bucket, prefix=f"{_RUNS_PREFIX}/", delimiter="/")
        list(it)  # must consume the page for `prefixes` to populate
        return sorted(p.rstrip("/").split("/")[-1] for p in (it.prefixes or set()))
    except Exception:
        log.exception("[storage] cannot list runs")
        return []


def has_remote_run(run_id: str) -> bool:
    if not is_enabled():
        return False
    bucket = _bucket()
    if bucket is None:
        return False
    try:
        return bucket.blob(f"{_RUNS_PREFIX}/{run_id}/run_manifest.json").exists()
    except Exception:
        return False


def download_run(run_id: str, dest: Path) -> int:
    """Download a run's artifacts to `dest`. Returns the file count."""
    if not is_enabled():
        return 0
    bucket = _bucket()
    if bucket is None:
        return 0
    prefix = f"{_RUNS_PREFIX}/{run_id}/"
    dest = Path(dest)
    count = 0
    try:
        for blob in bucket.client.list_blobs(bucket, prefix=prefix):
            rel = blob.name[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                blob.download_to_filename(str(target))
                count += 1
            except Exception:
                log.exception("[storage] failed to download %s", blob.name)
    except Exception:
        log.exception("[storage] download of run %s failed", run_id)
        return count
    if count:
        log.info("[storage] hydrated run %s: %d files", run_id, count)
    return count


def ensure_local(run_id: str, runs_root: Path) -> bool:
    """Make a run readable on local disk, fetching it if this instance lacks it.

    The instance that served a run keeps it locally; any other instance (or the
    same one after a restart) hydrates on first access. Returns whether the run
    is now present.
    """
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return False  # never let a path fragment escape runs_root
    local = Path(runs_root) / run_id
    if (local / "run_manifest.json").is_file():
        return True
    if not is_enabled():
        return False
    return download_run(run_id, local) > 0


def startup_log() -> None:
    if is_enabled():
        log.info(
            "[storage] run history is durable in gs://%s (archive_all=%s)",
            _BUCKET_NAME, _ARCHIVE_ALL,
        )
    else:
        log.warning(
            "[storage] UXAGENT_RUNS_BUCKET is not set — runs live only on this "
            "instance's disk and are LOST on restart or redeploy. Fine locally; "
            "on Cloud Run set it to keep run history."
        )
