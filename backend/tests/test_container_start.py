"""The container's server must start whatever happens to its virtual display.

Every deploy from 5d2690f to c3c9050 failed on Cloud Run with the startup probe
never finding port 8080 open (revisions ux-agent-00014 and -00015). The start
command had put the server inside `xvfb-run`, which waits for Xvfb before it
starts its command; gunicorn opens the port before it even loads the app, so a
port closed for four minutes means gunicorn never ran. The last image that
deployed (0faedd1) started gunicorn directly. Docker is not available where this
is developed, so these tests run the Dockerfile's real start command against
stand-in binaries instead.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

DOCKERFILE = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")


def _start_command() -> str:
    """The CMD instruction as the shell receives it (continuations joined)."""
    lines = DOCKERFILE.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("CMD "))
    parts = []
    while lines[i].rstrip().endswith("\\"):
        parts.append(lines[i].rstrip()[:-1])
        i += 1
    parts.append(lines[i])
    return " ".join(parts)[len("CMD "):]


def test_the_server_is_not_started_inside_a_wrapper_that_waits():
    cmd = _start_command()
    assert "xvfb-run" not in cmd
    assert "exec .venv/bin/gunicorn" in cmd


def test_the_display_is_started_in_the_background():
    cmd = _start_command()
    # " & " — the "&" inside "2>&1" is not the one that backgrounds it.
    head, sep, rest = cmd.partition(" & ")
    assert sep, "Xvfb must be backgrounded with &"
    assert head.strip().startswith("Xvfb :99"), "Xvfb first"
    assert rest.strip().startswith("export DISPLAY=:99"), "then the display for everything after it"


needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")


def _run(tmp_path: Path, xvfb: str):
    """Run the real start command with stand-ins for Xvfb and gunicorn."""
    bin_dir = tmp_path / "bin"
    venv_bin = tmp_path / ".venv" / "bin"
    bin_dir.mkdir()
    venv_bin.mkdir(parents=True)

    def script(path: Path, body: str):
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    behaviour = {"ok": "sleep 3\n", "fail": "echo 'cannot open display' >&2\nexit 1\n",
                 "hang": "sleep 30\n"}[xvfb]
    script(bin_dir / "Xvfb", behaviour)
    script(venv_bin / "gunicorn", 'echo "GUNICORN DISPLAY=$DISPLAY $*"\n')

    env = {**os.environ, "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}", "PORT": "8123"}
    command = _start_command().replace("/tmp/xvfb.log", (tmp_path / "xvfb.log").as_posix())
    started = time.monotonic()
    done = subprocess.run(["sh", "-c", command], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    return done, time.monotonic() - started


@needs_sh
@pytest.mark.parametrize("xvfb", ["ok", "fail", "hang"])
def test_the_server_starts_at_once_whatever_the_display_does(tmp_path, xvfb):
    done, elapsed = _run(tmp_path, xvfb)
    assert done.returncode == 0, done.stderr
    assert "GUNICORN DISPLAY=:99 --bind 0.0.0.0:8123 --workers 1 --threads 8 --timeout 0" in done.stdout
    assert "simulated_web_agent.main.app:app" in done.stdout
    assert elapsed < 10, f"the server waited {elapsed:.1f}s for the display"
