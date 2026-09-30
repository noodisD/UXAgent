"""The container must come up even when its virtual display cannot.

The 2026-09-30 deploy (Cloud Run revision ux-agent-00014) built and pushed
fine and then failed: "the container failed to start and listen on the port".
The start command had become `xvfb-run ... gunicorn`, and the image installed
xvfb with --no-install-recommends, which leaves out xauth and xkbcomp — so
xvfb-run exited ("xauth command not found") and gunicorn never ran. Docker is
not available where this is developed, so the image cannot be started before it
reaches Cloud Run. These tests run the Dockerfile's real start command against
stand-in binaries instead.
"""
import os
import shutil
import subprocess
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


def test_the_image_installs_what_xvfb_needs_to_start():
    """Recommends are skipped in that install, and playwright's own list has
    neither package: without them xvfb-run and Xvfb both refuse to start."""
    install = next(line for line in DOCKERFILE.splitlines() if "apt-get install" in line)
    assert "xvfb" in install and "xauth" in install and "x11-xkb-utils" in install


needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")


def _run(tmp_path: Path, xvfb: str):
    """Run the real start command with stand-ins for timeout, xvfb-run and gunicorn."""
    bin_dir = tmp_path / "bin"
    venv_bin = tmp_path / ".venv" / "bin"
    bin_dir.mkdir()
    venv_bin.mkdir(parents=True)
    log = tmp_path / "calls.log"

    def script(path: Path, body: str):
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    # `timeout 20 cmd...` -> cmd...  (the real one is not portable to this machine)
    script(bin_dir / "timeout", 'shift\nexec "$@"\n')
    script(bin_dir / "xvfb-run", f"""echo "xvfb-run $*" >> "{log.as_posix()}"
if [ "{xvfb}" = fail ]; then
  echo "xvfb-run: error: xauth command not found" >&2
  exit 3
fi
shift
case "$1" in --server-args=*) shift;; esac
exec "$@"
""")
    script(venv_bin / "gunicorn", 'echo "GUNICORN $*"\n')

    env = {**os.environ, "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}", "PORT": "8123"}
    done = subprocess.run(["sh", "-c", _start_command()], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls


@needs_sh
def test_a_working_display_runs_the_server_on_it(tmp_path):
    done, calls = _run(tmp_path, xvfb="ok")
    assert done.returncode == 0, done.stderr
    assert "GUNICORN --bind 0.0.0.0:8123 --workers 1 --threads 8 --timeout 0" in done.stdout
    assert "simulated_web_agent.main.app:app" in done.stdout
    assert len(calls) == 2 and calls[0].endswith(" true"), calls  # the probe, then the real start
    assert "--server-args=-screen 0 1440x900x24" in calls[1], calls


@needs_sh
def test_a_broken_display_still_starts_the_server(tmp_path):
    """The failure that took production down: xvfb-run cannot run."""
    done, calls = _run(tmp_path, xvfb="fail")
    assert done.returncode == 0, done.stderr
    assert "GUNICORN --bind 0.0.0.0:8123 --workers 1 --threads 8 --timeout 0" in done.stdout
    assert "starting without a virtual display" in done.stderr
    assert len(calls) == 1, "only the probe ran; the server is not wrapped in it"
