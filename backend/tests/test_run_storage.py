"""Cloud Run wipes runs/ on every restart, so run history used to last only as
long as one container. run_storage copies finished runs to a bucket and fetches
them back on demand.

Two properties matter most and are what these tests pin down:

  1. Unconfigured it must be completely inert — no bucket, no cloud client, no
     behaviour change. That is what keeps local development and the rest of the
     suite running without credentials.
  2. It must never turn a storage problem into a failed run. The run already
     succeeded on local disk; an upload error is not its verdict.
"""
from pathlib import Path

import pytest

from simulated_web_agent.main import run_storage


# --- unconfigured: inert ---------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(run_storage, "_BUCKET_NAME", "")
    assert run_storage.is_enabled() is False


def test_every_operation_noops_when_disabled(monkeypatch, tmp_path):
    """No cloud client is constructed, so no credentials are needed."""
    monkeypatch.setattr(run_storage, "_BUCKET_NAME", "")
    monkeypatch.setattr(
        run_storage, "_bucket",
        lambda: pytest.fail("must not touch GCS when unconfigured"),
    )
    assert run_storage.upload_run(tmp_path) == 0
    assert run_storage.read_index() == []
    assert run_storage.remote_run_ids() == []
    assert run_storage.download_run("r1", tmp_path) == 0
    assert run_storage.has_remote_run("r1") is False
    run_storage.merge_index_row({"id": "r1"})  # must not raise


# --- what gets archived ----------------------------------------------------

@pytest.mark.parametrize("rel", [
    "api_trace/api_trace_1.json",
    "raw_html/raw_html_0.html",
    "simp_html/simp_html_0.html",
    "screenshot/screenshot_3_full_page.png",
    "persona/api_trace/x.json",
])
def test_debug_artifacts_are_excluded(rel, monkeypatch):
    """These dominate the size (~90%) and nothing in the dashboard reads them:
    results_api skips full-page shots when picking a step's screenshot."""
    monkeypatch.setattr(run_storage, "_ARCHIVE_ALL", False)
    assert run_storage._is_excluded(Path(rel)) is True


@pytest.mark.parametrize("rel", [
    "run_manifest.json",
    "aggregated_report.json",
    "aggregated_report.html",
    "persona/heuristic_findings.json",
    "persona/memory_trace.json",
    "persona/observation_trace/observation_trace_2.txt",
    "persona/screenshot/screenshot_3.png",
    "persona/a11y_snapshots/a11y_snapshot_0.json",
])
def test_everything_the_dashboard_reads_is_kept(rel, monkeypatch):
    monkeypatch.setattr(run_storage, "_ARCHIVE_ALL", False)
    assert run_storage._is_excluded(Path(rel)) is False


def test_archive_all_keeps_the_debug_artifacts_too(monkeypatch):
    monkeypatch.setattr(run_storage, "_ARCHIVE_ALL", True)
    assert run_storage._is_excluded(Path("api_trace/api_trace_1.json")) is False
    assert run_storage._is_excluded(Path("screenshot/s_0_full_page.png")) is False


# --- hydration -------------------------------------------------------------

def test_ensure_local_is_satisfied_by_a_run_already_on_disk(monkeypatch, tmp_path):
    """The instance that executed a run must not re-download it."""
    monkeypatch.setattr(run_storage, "_BUCKET_NAME", "b")
    monkeypatch.setattr(
        run_storage, "download_run",
        lambda *a, **k: pytest.fail("should not download a local run"),
    )
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "run_manifest.json").write_text("{}", encoding="utf-8")
    assert run_storage.ensure_local("r1", tmp_path) is True


@pytest.mark.parametrize("bad", ["../etc", "a/b", "a\\b", ".hidden", ""])
def test_ensure_local_rejects_path_traversal(bad, monkeypatch, tmp_path):
    """run_id reaches this straight from the URL."""
    monkeypatch.setattr(run_storage, "_BUCKET_NAME", "b")
    monkeypatch.setattr(
        run_storage, "download_run",
        lambda *a, **k: pytest.fail("must not act on a traversal attempt"),
    )
    assert run_storage.ensure_local(bad, tmp_path) is False


# --- failures are contained -----------------------------------------------

def test_upload_survives_a_broken_bucket(monkeypatch, tmp_path):
    """A storage outage must not propagate into the pipeline."""
    monkeypatch.setattr(run_storage, "_BUCKET_NAME", "b")

    class Boom:
        def blob(self, *a, **k):
            raise RuntimeError("GCS down")

    monkeypatch.setattr(run_storage, "_bucket", lambda: Boom())
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    assert run_storage.upload_run(tmp_path) == 0  # no raise


def test_read_index_survives_malformed_json(monkeypatch):
    monkeypatch.setattr(run_storage, "_BUCKET_NAME", "b")

    class Blob:
        def exists(self):
            return True

        def download_as_bytes(self):
            return b"not json{"

    class Bucket:
        def blob(self, *a, **k):
            return Blob()

    monkeypatch.setattr(run_storage, "_bucket", lambda: Bucket())
    assert run_storage.read_index() == []


# --- round trip through an in-memory bucket --------------------------------
#
# The mechanism that actually has to hold: a run uploaded from one container is
# readable by the next one, which has an empty disk. Exercised against a fake
# bucket so it runs in CI without credentials.

class FakeBlob:
    def __init__(self, store, name):
        self._store, self.name = store, name

    def upload_from_filename(self, path):
        self._store[self.name] = Path(path).read_bytes()

    def upload_from_string(self, data, content_type=None):
        self._store[self.name] = data.encode("utf-8") if isinstance(data, str) else data

    def download_to_filename(self, path):
        Path(path).write_bytes(self._store[self.name])

    def download_as_bytes(self):
        return self._store[self.name]

    def exists(self):
        return self.name in self._store


class FakeBucket:
    def __init__(self, store):
        self._store = store
        self.client = self

    def blob(self, name):
        return FakeBlob(self._store, name)

    # mirrors google.cloud.storage.Client.list_blobs, including the real
    # iterator's `prefixes` attribute (only populated once consumed)
    def list_blobs(self, bucket, prefix="", delimiter=None):
        names = [n for n in sorted(self._store) if n.startswith(prefix)]
        if delimiter:
            prefixes = {
                prefix + n[len(prefix):].split(delimiter)[0] + delimiter
                for n in names if delimiter in n[len(prefix):]
            }
            return _FakeIterator([], prefixes)
        return _FakeIterator([FakeBlob(self._store, n) for n in names], set())


class _FakeIterator:
    def __init__(self, items, prefixes):
        self._items, self.prefixes = items, prefixes

    def __iter__(self):
        return iter(self._items)


@pytest.fixture
def fake_bucket(monkeypatch):
    store: dict[str, bytes] = {}
    monkeypatch.setattr(run_storage, "_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(run_storage, "_ARCHIVE_ALL", False)
    monkeypatch.setattr(run_storage, "_bucket", lambda: FakeBucket(store))
    return store


def _make_run(root: Path, run_id: str) -> Path:
    """A run dir with one persona: keepers plus the bulk that must be dropped."""
    run_dir = root / run_id
    persona = run_dir / "p1"
    (persona / "screenshot").mkdir(parents=True)
    (persona / "api_trace").mkdir()
    (run_dir / "run_manifest.json").write_text(
        '{"run_id": "%s", "site": "x.pl", "status": "done"}' % run_id, encoding="utf-8"
    )
    (run_dir / "aggregated_report.html").write_text("<h1>report</h1>", encoding="utf-8")
    (persona / "heuristic_findings.json").write_text("[]", encoding="utf-8")
    (persona / "screenshot" / "screenshot_0.png").write_bytes(b"png-viewport")
    (persona / "screenshot" / "screenshot_0_full_page.png").write_bytes(b"x" * 5000)
    (persona / "api_trace" / "api_trace_1.json").write_bytes(b"y" * 5000)
    return run_dir


def test_upload_then_download_onto_a_fresh_disk(fake_bucket, tmp_path):
    run_dir = _make_run(tmp_path / "runs", "R1")
    assert run_storage.upload_run(run_dir) == 4  # the keepers only

    fresh = tmp_path / "other_instance"
    assert run_storage.download_run("R1", fresh / "R1") == 4

    assert (fresh / "R1" / "run_manifest.json").is_file()
    assert (fresh / "R1" / "aggregated_report.html").read_text(encoding="utf-8") == "<h1>report</h1>"
    assert (fresh / "R1" / "p1" / "screenshot" / "screenshot_0.png").read_bytes() == b"png-viewport"
    # the 90% that is deliberately not archived
    assert not (fresh / "R1" / "p1" / "api_trace").exists()
    assert not (fresh / "R1" / "p1" / "screenshot" / "screenshot_0_full_page.png").exists()


def test_index_accumulates_rows_and_replaces_by_id(fake_bucket):
    run_storage.merge_index_row({"id": "R1", "started_at": "2026-01-01", "score": 1})
    run_storage.merge_index_row({"id": "R2", "started_at": "2026-02-01"})
    run_storage.merge_index_row({"id": "R1", "started_at": "2026-01-01", "score": 9})

    rows = run_storage.read_index()
    assert [r["id"] for r in rows] == ["R2", "R1"]          # newest first
    assert [r for r in rows if r["id"] == "R1"][0]["score"] == 9


def test_remote_run_ids_and_ensure_local_hydrate(fake_bucket, tmp_path):
    _make_run(tmp_path / "runs", "R1")
    run_storage.upload_run(tmp_path / "runs" / "R1")

    assert run_storage.remote_run_ids() == ["R1"]
    assert run_storage.has_remote_run("R1") is True

    empty = tmp_path / "fresh_runs"
    empty.mkdir()
    assert run_storage.ensure_local("R1", empty) is True
    assert (empty / "R1" / "run_manifest.json").is_file()
    assert run_storage.ensure_local("nope", empty) is False


def test_history_merges_the_bucket_with_local_disk(fake_bucket, tmp_path, monkeypatch):
    """The point of the whole feature: a run this instance never executed still
    appears in history, and a local run is not duplicated by its uploaded row."""
    from simulated_web_agent.main import results_api

    runs_root = tmp_path / "runs"
    local = _make_run(runs_root, "LOCAL")
    run_storage.merge_index_row(results_api.build_history_row(local))
    run_storage.merge_index_row(
        {"id": "OLD", "site": "old.pl", "started_at": "2020-01-01", "status": "complete"}
    )

    rows = results_api.list_runs(runs_root)
    ids = [r["id"] for r in rows]
    assert ids.count("LOCAL") == 1        # not duplicated
    assert "OLD" in ids                   # survives although absent locally
