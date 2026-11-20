"""Bounded external CLI capture, retaining native bytes and reaping owned groups."""

import os
import selectors
import signal
import subprocess
import time

from .jsonio import MAX_LINE, encoded, require, write_new


def invoke(executable, project, arguments, directory, environment, seconds):
    argv = [str(executable), "call", "ctx_search", "--project-root", str(project),
            "--json", encoded(arguments).decode().strip()]
    process = subprocess.Popen(argv, cwd=project, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    deadline = time.monotonic() + seconds
    try:
        while selector.get_map():
            require(time.monotonic() < deadline, "LeanCTX query deadline")
            for key, _ in selector.select(min(.2, max(0, deadline - time.monotonic()))):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                else:
                    buffers[key.data].extend(data)
                    require(len(buffers[key.data]) <= MAX_LINE, "LeanCTX native output limit")
        process.wait(timeout=max(.01, deadline - time.monotonic()))
        require(process.returncode == 0, "LeanCTX CLI failed; inspect native-stderr.log")
        return buffers["stdout"].decode("utf-8", errors="strict")
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
        selector.close()
        process.stdout.close()
        process.stderr.close()
        for name, payload in buffers.items():
            write_new(directory / ("native-" + name + ".log"), bytes(payload))
