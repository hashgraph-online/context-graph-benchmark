"""Conservative source-inventory hygiene; not a substitute for secret review."""

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {".github", "adapters", "datasets", "docs", "examples", "profiles", "test-packs",
               "requirements", "schemas", "scripts", "src", "suites", "tests", "third-party"}
FILES = {"LICENSE", "NOTICE", "pyproject.toml", ".gitignore", "CITATION.cff"}
FORBIDDEN = {".cgb", "lab", ".cache", "__pycache__", "node_modules", "target", "dist", "build"}
SUFFIXES = {".pem", ".key", ".safetensors", ".gguf", ".whl", ".zip", ".pyc", ".tgz"}
PATTERNS = [re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/|/" + "Volumes" + "/"),
            re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]


def candidates():
    if (ROOT / ".git").exists():
        process = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT, capture_output=True, check=True)
        return [Path(p.decode()) for p in process.stdout.split(b"\0") if p]
    return [p.relative_to(ROOT) for p in ROOT.rglob("*") if p.is_file()
            and (p.relative_to(ROOT).parts[0] in DIRECTORIES or len(p.relative_to(ROOT).parts) == 1)]


def main():
    errors, count = [], 0
    for relative in candidates():
        parts = relative.parts
        if any(p in FORBIDDEN or p.startswith(".venv") for p in parts):
            # In a Git repository even ignored paths are rejected if tracked.
            if (ROOT / ".git").exists():
                errors.append((str(relative), "private/generated directory tracked"))
            continue
        path = ROOT / relative
        if path.is_symlink() or not path.is_file():
            errors.append((str(relative), "non-regular source file"))
            continue
        if (len(parts) > 1 and parts[0] not in DIRECTORIES) or (len(parts) == 1 and relative.name not in FILES and relative.suffix != ".md"):
            errors.append((str(relative), "outside publication allowlist"))
        if relative.suffix in SUFFIXES or relative.name.startswith('.env') or path.stat().st_size > 1024**2:
            errors.append((str(relative), "secret/model/archive/generated payload type or excessive size"))
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append((str(relative), "binary payload"))
            continue
        for pattern in PATTERNS:
            if pattern.search(text):
                errors.append((str(relative), "machine path or possible credential material; inspect locally"))
                break
        count += 1
    for path, reason in errors:
        print(path + ": " + reason, file=sys.stderr)
    print(str(count) + " source files checked; " + str(len(errors)) + " issue(s).")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
