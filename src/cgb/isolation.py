"""Explicit local adapter confinement; no downloads or implicit privilege changes.

The trusted supervisor owns labels and model calls. Adapter sandboxes receive
only declared runtime paths, an isolated writable directory and stdin inputs.
These controls are tested, not a claim of kernel or hardware attestation.
"""

import json
import os
import platform
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from .jsonio import Invalid, decode, digest, encoded, require, write_json

MODES = ("auto", "macos", "bubblewrap", "process")


def available():
    return {"macos": sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
            "bubblewrap": sys.platform.startswith("linux") and shutil.which("bwrap") is not None,
            "process": True}


def select(mode):
    require(mode in MODES, "unknown isolation mode")
    options = available()
    if mode == "auto":
        mode = "macos" if options["macos"] else "bubblewrap" if options["bubblewrap"] else "process"
    require(options.get(mode) is True, "requested isolation runtime is unavailable")
    return mode


def read_roots(command, artifacts, protected):
    """Resolve explicit runtime inputs, never a root/home/project-wide grant."""
    protected = Path(protected).resolve()
    candidates = [Path(sys.base_prefix), Path(sys.prefix) / "lib", Path(sys.prefix) / "pyvenv.cfg", Path(__file__).parent.parent]
    candidates += [Path(a["path"]) for a in artifacts]
    candidates += [Path(p) for p in command if isinstance(p, str) and p.startswith("/") and Path(p).exists()]
    roots = []
    for candidate in candidates:
        path = candidate.resolve()
        require(path != Path("/") and path != Path.home() and not protected.is_relative_to(path) and not path.is_relative_to(protected),
                "adapter read root would expose protected grading files")
        require(not any(ord(c) < 32 for c in str(path)), "control character in runtime path")
        if path.exists() and path not in roots:
            roots.append(path)
    return roots


def _mac_profile(readable, writable, protected=None):
    quote = lambda p: json.dumps(str(p), ensure_ascii=False)
    paths = list(readable)
    paths += [Path(p) for p in ("/System/Library", "/usr/lib", "/usr/share", "/Library/Apple/System/Library") if Path(p).exists()]
    paths += [Path(p) for p in ("/dev/null", "/dev/random", "/dev/urandom") if Path(p).exists()]
    rules = ["(version 1)", "(deny default)", '(import "system.sb")', "(allow process-fork)", "(allow process-exec)",
             "(allow signal (target self))", "(allow file-read-metadata)",
             '(allow mach-lookup (global-name "com.apple.cfprefsd.daemon") (global-name "com.apple.system.logger"))',
             '(allow sysctl-read (sysctl-name-regex "^hw\\\\.") (sysctl-name-regex "^kern\\\\.os"))']
    for path in paths:
        rules.append("(allow file-read* file-map-executable (" + ("subpath " if path.is_dir() else "literal ") + quote(path) + "))")
    rules.append("(allow file-read* file-write* (subpath " + quote(writable) + "))")
    if protected is not None:
        rules.append("(deny file-read* (subpath " + quote(Path(protected).resolve()) + "))")
    rules.append('(deny network-inbound network-outbound (remote ip "*:*"))')
    return "\n".join(rules) + "\n"


def prepare(command, directory, tokenizer, artifacts, protected, mode="auto"):
    mode = select(mode)
    directory = Path(directory).resolve()
    roots = read_roots(command, artifacts, protected) + [Path(tokenizer).resolve()]
    temporary = directory / "tmp"
    temporary.mkdir(exist_ok=False)
    environment = {"TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary)}
    profile = None
    if mode == "macos":
        profile = _mac_profile(roots, directory, protected)
        argv = ["/usr/bin/sandbox-exec", "-p", profile, *command]
    elif mode == "bubblewrap":
        mounts = list(roots) + [Path(p) for p in ("/usr", "/lib", "/lib64", "/etc/ld.so.cache", "/etc/localtime") if Path(p).exists()]
        argv = [shutil.which("bwrap"), "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for path in sorted(set(mounts), key=lambda p: (len(p.parts), str(p))):
            argv += ["--ro-bind", str(path), str(path)]
        argv += ["--bind", str(directory), str(directory), "--chdir", str(directory), "--", *command]
    else:
        argv = list(command)
    specification = {"schema": "cgb.adapter-isolation.v1", "mode": mode,
        "read_paths": [str(p) for p in roots], "write_path": str(directory),
        "network": "internet-and-host-loopback-denied" if mode != "process" else "unconfined",
        "protected_root": str(Path(protected).resolve()), "profile_sha256": digest(encoded(profile if profile else argv)),
        "os": platform.platform(),
        "os_profile_sha256": digest(Path("/System/Library/Sandbox/Profiles/system.sb").read_bytes()) if mode == "macos" else None,
        "scope": "Local runtime confinement. Public system metadata is readable; arbitrary hostile binaries require an independently managed VM/container."}
    write_json(directory / "isolation.json", specification)
    return argv, environment, specification


def probe(directory, tokenizer, protected, mode="auto"):
    """Verify real denied reads, symlink reads and a live loopback listener."""
    directory, protected = Path(directory), Path(protected)
    directory.mkdir(parents=True, exist_ok=False)
    protected.mkdir(parents=True, exist_ok=True)
    sentinel = protected / ("sandbox-probe-" + digest(encoded(str(directory.resolve())))[:16] + ".txt")
    with sentinel.open("x") as stream:
        stream.write("synthetic probe; never an engine input\n")
    sentinel.chmod(0o600)
    link = directory / "denied-link"
    link.symlink_to(sentinel.resolve())
    program = """import json,socket,sys
from pathlib import Path
def denied(path):
    try: Path(path).read_bytes(); return False
    except (PermissionError,FileNotFoundError): return True
network=False
try:
    with socket.create_connection(('127.0.0.1',int(sys.argv[3])),timeout=2): pass
except PermissionError: network=True
except OSError as error: network=error.errno in (1,13,101,113)
print(json.dumps({'read_denied':denied(sys.argv[1]),'symlink_denied':denied(sys.argv[2]),'network_denied':network}),flush=True)
"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        command = [sys.executable, "-I", "-c", program, str(sentinel.resolve()), str(link.absolute()), str(listener.getsockname()[1])]
        # Probe paths are test targets, not declared runtime input arguments.
        argv, extra, specification = prepare([sys.executable], directory, tokenizer, [], protected, mode)
        argv = argv[:-1] + command
        environment = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        environment.update(extra, PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.run(argv, cwd=directory, env=environment, capture_output=True, timeout=15)
    write_json(directory / "probe-process.json", {"returncode": process.returncode,
        "stdout": process.stdout.decode(errors="replace"), "stderr": process.stderr.decode(errors="replace")})
    link.unlink()  # Only the synthetic link this probe created; evidence bundles reject all symlinks.
    require(process.returncode == 0, "isolation probe failed: " + process.stderr.decode(errors="replace")[:1000])
    result = decode(process.stdout)
    enforced = specification["mode"] != "process" and all(result.get(k) is True for k in
                ("read_denied", "symlink_denied", "network_denied"))
    require(specification["mode"] == "process" or enforced, "isolation runtime did not enforce required controls")
    result = {**result, "mode": specification["mode"], "enforced": enforced,
              "profile_sha256": specification["profile_sha256"], "independently_attested": False}
    write_json(directory / "probe.json", result)
    return result
