"""Workspace paths, atomic progress files and bounded benchmark subprocesses."""

from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.resources import files
import os
from pathlib import Path, PurePosixPath
import selectors
import signal
import subprocess
import time

from .jsonio import Invalid, encoded, require, write_json, write_new

MAX_LOG = 16 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path, value):
    """Only for the derived live index; original attempt files are create-new."""
    path = Path(path)
    temporary = path.with_name(path.name + ".next")
    require(not path.is_symlink() and not temporary.is_symlink(), "symlink live index")
    with temporary.open("wb") as stream:
        stream.write(encoded(value))
    os.replace(temporary, path)


def inside(root, relative):
    require(isinstance(relative, str), "path must be a string")
    value = PurePosixPath(relative)
    require(value.parts and not value.is_absolute() and ".." not in value.parts and
            value.as_posix() == relative and "\\" not in relative, "invalid project-relative path")
    path = Path(root)
    require(path.is_dir() and not path.is_symlink(), "invalid project root")
    for part in value.parts:
        path = path / part
        require(not path.is_symlink(), "symlink project path")
    return path


def command(args, cwd, destination, seconds, stop=None, heartbeat=None):
    """Bounded logs/deadline and process-group cleanup, without a shell."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / "command.json", {"argv": args, "started_at": now(), "timeout_seconds": seconds})
    begin = time.monotonic()
    deadline = begin + max(.01, seconds)
    with (destination / "stdout.log").open("xb") as out, (destination / "stderr.log").open("xb") as err:
        try:
            environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "TMPDIR") if key in os.environ}
            environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
            child = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                     env=environment)
        except OSError as exc:
            result = {"status": "launch_error", "returncode": None, "seconds": time.monotonic()-begin,
                      "message": str(exc), "pid": None}
            write_json(destination / "outcome.json", result)
            return result
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ, out)
        selector.register(child.stderr, selectors.EVENT_READ, err)
        counts = {out: 0, err: 0}
        status, last_heartbeat = "completed", 0
        try:
            while selector.get_map():
                if heartbeat is not None and time.monotonic()-last_heartbeat >= 2:
                    heartbeat()
                    last_heartbeat = time.monotonic()
                if stop is not None and stop():
                    status = "stopped"
                    break
                if time.monotonic() >= deadline:
                    status = "timeout"
                    break
                for key, _ in selector.select(min(.25, max(.01, deadline-time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = MAX_LOG-counts[key.data]
                    key.data.write(chunk[:max(0, remaining)])
                    key.data.flush()
                    counts[key.data] += len(chunk)
                    if counts[key.data] > MAX_LOG:
                        status = "output_limit"
                        break
                if status != "completed":
                    break
            if status == "completed":
                try:
                    child.wait(timeout=max(.01, deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    status = "timeout"
        finally:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGINT)
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=2)
                except ProcessLookupError:
                    child.wait(timeout=2)
            selector.close()
            for stream in (child.stdout, child.stderr, child.stdin):
                if stream is not None and not stream.closed:
                    stream.close()
    result = {"status": status, "returncode": child.returncode, "pid": child.pid,
              "seconds": time.monotonic()-begin}
    write_json(destination / "outcome.json", result)
    return result


@contextmanager
def workspace_lock(root):
    import fcntl
    root = Path(root).absolute()
    require(root.is_dir() and not root.is_symlink(), "invalid project root")
    directory = root/"lab"
    require(not directory.is_symlink(), "symlink lab directory")
    directory.mkdir(exist_ok=True)
    path = directory/"controller.lock"
    require(not path.is_symlink(), "symlink controller lock")
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Invalid("another benchmark job is active in this workspace") from exc
        yield

TEMPLATE_DIRS = ("adapters", "datasets/synthetic", "suites", "profiles")


def template_directory(part):
    """Find canonical public inputs in an installed wheel or a source checkout."""
    require(part in TEMPLATE_DIRS, "unknown workspace input directory")
    installed = files("cgb").joinpath("data/examples", *part.split("/"))
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parents[2] / part


def initialize(output):
    """Materialize the public JSON inputs in a new directory without running an engine."""
    root = Path(output).absolute()
    root.mkdir(parents=True, exist_ok=False)
    for part in TEMPLATE_DIRS:
        for entry in template_directory(part).iterdir():
            if entry.name.endswith(".json") and entry.is_file():
                write_new(root / part / entry.name, entry.read_bytes())
    return {"workspace": str(root)}
