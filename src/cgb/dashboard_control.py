"""Local dashboard jobs use the same bounded CLI as terminal runs."""

from datetime import datetime, timezone
import copy
from pathlib import Path
import secrets
import sys
import threading
import time

from .workspace import atomic_json, command, inside, workspace_lock
from .jsonio import Invalid, read_json, require, write_json


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DashboardController:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.active = None
        self.thread = None
        self.stop_event = threading.Event()

    def status(self):
        with self.lock:
            return {"active_job": copy.deepcopy(self.active), "can_start": self.active is None}

    def start(self, value):
        directories = {"platform-compare": ("Context graph comparison", "comparisons"),
                       "platform-soak": ("Portable agent soak", "portable-soaks"),
                       "platform-assurance": ("Security and privacy packs", "assurance"),
                       "platform-study": ("Complete benchmark study", "studies")}
        require(type(value) is dict and isinstance(value.get("kind"), str) and value["kind"] in directories,
                "unknown benchmark job kind")
        kind = value["kind"]
        label, folder = directories[kind]
        name = value.get("name", label)
        require(isinstance(name, str) and 0 < len(name.strip()) <= 120, "invalid run name")
        config = None
        if kind == "platform-assurance":
            require(set(value) <= {"kind", "name", "engines", "reader", "packs", "isolation", "baseline"}, "invalid assurance settings")
            from .test_packs.runtime import settings
            from .test_packs.registry import reference
            config, _ = settings(self.root, value.get("engines", []), value.get("reader"), value.get("packs"), value.get("isolation", "auto"), value.get("baseline"))
            config["packs"] = [reference(e["manifest"]) for e in config["packs"]]
        elif kind == "platform-study":
            require(set(value) <= {"kind", "name", "engines", "reader", "hours", "pilot_seconds", "isolation", "baseline"}, "invalid study settings")
            from .macro import settings
            config = settings(self.root, value.get("engines", []), value.get("reader"), value.get("hours", 1), value.get("pilot_seconds"), value.get("baseline"), value.get("isolation", "auto"))
        elif kind in ("platform-compare", "platform-soak"):
            from .comparison import settings
            require(set(value) <= {"kind", "name", "engines", "profiles", "budgets", "replicates", "hours", "pilot_seconds", "reader", "baseline", "assurance", "isolation"}, "invalid platform settings")
            selected = value.get("engines", [])
            if kind == "platform-compare":
                require(set(value) <= {"kind", "name", "engines", "profiles", "budgets", "replicates"}, "invalid comparison settings")
                config = {"engines": selected, "profiles": value.get("profiles", ["evidence", "graph"]),
                          "budgets": value.get("budgets", [512, 2048, 4096]), "replicates": value.get("replicates", 1), "name": name}
                settings(self.root, selected, config["profiles"], config["budgets"], config["replicates"])
            else:
                require(set(value) <= {"kind", "name", "engines", "hours", "pilot_seconds", "reader", "baseline", "assurance", "isolation"}, "invalid soak settings")
                from .portable_soak import validate_settings
                config = validate_settings(self.root, selected, value.get("hours", 1), value.get("pilot_seconds"), value.get("reader"), value.get("baseline"), value.get("assurance", False), value.get("isolation", "auto"))
        with self.lock:
            require(self.active is None, "another dashboard job is running; stop it or wait for completion")
            with workspace_lock(self.root):
                pass
            identity = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
            directory = inside(self.root, "lab/jobs/" + identity)
            directory.mkdir(parents=True, exist_ok=False)
            job = {"schema": "cgb.dashboard-job.v1", "id": identity, "name": name.strip(), "kind": kind,
                   "status": "running", "started_at": timestamp(), "updated_at": timestamp(),
                   "path": directory.relative_to(self.root).as_posix(), "session": None,
                   "summary": "Starting " + kind + ".", "process": None, "result": None}
            write_json(directory / "config.json", config)
            job["session"] = "lab/" + folder + "/" + identity
            atomic_json(directory / "job.json", job)
            self.active = job
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._execute, args=(directory, job, config), daemon=True,
                                           name="cgb-dashboard-job")
            self.thread.start()
            return copy.deepcopy(job)

    def _execute(self, directory, job, config):
        def save():
            with self.lock:
                job["updated_at"] = timestamp()
                atomic_json(directory / "job.json", job)
        try:
            if job["kind"] in ("platform-assurance", "platform-study"):
                study = job["kind"] == "platform-study"
                argv = [sys.executable, "-m", "cgb", *( ["platform", "study"] if study else ["test-pack", "run"] ),
                    "--root", str(self.root), "--output", str(self.root / job["session"]), "--engines", *config["engines"],
                    "--reader", config["reader_path"], "--baseline", config["baseline"], "--isolation", config["isolation"]]
                if study:
                    argv += ["--hours", str(config["hours"])]
                    if config["pilot_seconds"] is not None:
                        argv += ["--pilot-seconds", str(config["pilot_seconds"])]
                else:
                    argv += ["--packs", *config["packs"]]
                stop_seen = None
                def cooperative():
                    nonlocal stop_seen
                    if not self.stop_event.is_set(): return False
                    if stop_seen is None: stop_seen = time.monotonic()
                    return time.monotonic() - stop_seen > 130
                seconds = config["seconds"]*len(config["engines"])+2400 if study else config["wall_seconds"]
                with workspace_lock(self.root):
                    outcome = command(argv, self.root, directory / "command", seconds, stop=cooperative, heartbeat=save)
                path = self.root / job["session"] / "state.json"
                state = read_json(path) if path.is_file() else None
                job.update(process=outcome, status="stopped" if self.stop_event.is_set() else "passed" if outcome["returncode"] == 0 and state and state["status"] == "completed" else "failed",
                           summary=state.get("summary", state["status"]) if state else "Setup failed; original logs retained.")
            elif job["kind"] in ("platform-compare", "platform-soak"):
                soak = job["kind"] == "platform-soak"
                argv = [sys.executable, "-m", "cgb", "platform", "soak" if soak else "compare", "--root", str(self.root),
                        "--output", str(self.root / job["session"]), "--engines", *config["engines"]]
                if soak:
                    argv += ["--hours", str(config["hours"])]
                    if config["pilot_seconds"] is not None:
                        argv += ["--pilot-seconds", str(config["pilot_seconds"])]
                    if config["reader_path"] is not None:
                        argv += ["--reader", config["reader_path"]]
                    argv += ["--baseline", config["baseline"]]
                    if config.get("assurance"):
                        argv += ["--assurance", "--isolation", config["isolation"]]
                else:
                    argv += ["--name", config["name"], "--profiles", *config["profiles"], "--budgets", *map(str, config["budgets"]), "--replicates", str(config["replicates"])]
                stop_seen = None
                def cooperative_stop():
                    nonlocal stop_seen
                    if not self.stop_event.is_set():
                        return False
                    if not soak:
                        return True
                    if stop_seen is None:
                        stop_seen = time.monotonic()
                    return time.monotonic() - stop_seen > 90
                seconds = (config["seconds"] * len(config["engines"]) + 600) if soak else 1800
                with workspace_lock(self.root):
                    outcome = command(argv, self.root, directory / "command", seconds, stop=cooperative_stop, heartbeat=save)
                state_path = self.root / job["session"] / "state.json"
                state = read_json(state_path) if state_path.is_file() else None
                job.update(process=outcome, status="stopped" if self.stop_event.is_set() else "passed" if outcome["returncode"] == 0 and state and state["status"] == "completed" else "failed",
                           summary=state.get("summary", state["status"]) if state else "Run failed before initialization; inspect the retained log.")
        except (Invalid, OSError, ValueError, KeyError) as exc:
            job.update(status="failed", summary=type(exc).__name__ + ": " + str(exc))
        finally:
            with self.lock:
                job["finished_at"] = timestamp()
                save()
                self.active = None

    def stop(self, identity):
        with self.lock:
            require(self.active is not None and identity == self.active["id"], "job is no longer running in this dashboard")
            self.stop_event.set()
            self.active["summary"] = "Stop requested; preserving original logs and partial attempts."
            if self.active["session"]:
                marker = inside(self.root, self.active["session"] + "/STOP")
                if marker.parent.is_dir() and not marker.exists():
                    marker.write_bytes(b"Stop requested from the local dashboard.\n")
                if self.active["kind"] == "platform-study":
                    nested = inside(self.root, self.active["session"] + "/soak/STOP")
                    if nested.parent.is_dir() and not nested.exists():
                        nested.write_bytes(b"Stop requested for study soak.\n")
            return {"stop_requested": identity}

    def close(self):
        with self.lock:
            if self.active is not None:
                self.stop(self.active["id"])
            thread = self.thread
        if thread is not None:
            thread.join(timeout=10)
