"""Loopback HTTP dashboard for portable studies and their public summaries."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

from .workspace import inside
from .jsonio import Invalid, encoded, require


def preview(path, limit=196608, tail=False):
    size = path.stat().st_size
    with path.open("rb") as stream:
        if tail and size > limit:
            stream.seek(size-limit)
        result = stream.read(limit).decode("utf-8", errors="replace")
    return ("[Truncated preview; original retained on disk.]\n" if size > limit else "") + result


def asset(name):
    return files("cgb").joinpath("data/dashboard", name).read_bytes()


def handler(root, controller=None):
    root = Path(root).absolute()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def send(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            route = urlsplit(self.path)
            try:
                # No CORS, writes, arbitrary project file serving, or public bind address.
                host = self.headers.get("Host", "").split(":")[0]
                require(host in ("127.0.0.1", "localhost"), "invalid dashboard host")
                if route.path in ("/", "/index.html"):
                    html = asset("platform.html").decode()
                    if controller is not None:
                        html = html.replace("<!--CONTROL-->", '<meta name="cgb-control-token" content="'+controller.token+'">')
                    return self.send(html.encode(), "text/html; charset=utf-8")
                if route.path in ("/platform.js", "/platform.css"):
                    kind = "text/javascript" if route.path.endswith('.js') else "text/css"
                    return self.send(asset(route.path[1:]), kind+"; charset=utf-8")
                if route.path == "/api/platform":
                    from .platform import collect as collect_platform
                    data = collect_platform(root)
                    if controller is not None:
                        data["controller"] = controller.status()
                    return self.send(encoded(data), "application/json; charset=utf-8")
                if route.path == "/api/artifact":
                    relative = parse_qs(route.query).get("path", [""])[0]
                    parts = Path(relative).parts
                    allowed = (len(parts) == 4 and parts[1] in ("comparisons", "portable-soaks", "assurance", "studies")
                               or len(parts) == 5 and parts[1] == "studies" and parts[3] in ("comparison", "soak"))
                    require(allowed and parts[0] == "lab" and parts[-1] in ("plan.json", "registration.json", "state.json", "result.json"),
                            "only public run summaries are served")
                    path = inside(root, relative)
                    require(path.is_file() and path.stat().st_size <= 32*1024*1024, "artifact missing or too large")
                    require(path.suffix in (".json", ".jsonl", ".log", ".md", ".diff", ".txt", ".html"), "artifact type unavailable")
                    # Even reports are plain text here. The dashboard never embeds untrusted HTML.
                    return self.send(preview(path, tail=parse_qs(route.query).get("tail") == ["1"]).encode(), "text/plain; charset=utf-8")
                return self.send(b"Not found", "text/plain", 404)
            except (Invalid, OSError, ValueError) as exc:
                return self.send(str(exc).encode(), "text/plain; charset=utf-8", 400)

        def do_POST(self):
            if controller is None or self.path not in ("/api/jobs/start", "/api/jobs/stop"):
                return self.send(b"Unknown control route.", "text/plain", 405)
            try:
                host = self.headers.get("Host", "")
                require(host.split(":")[0] in ("127.0.0.1", "localhost"), "invalid dashboard host")
                require(self.headers.get("Origin") == "http://" + host, "dashboard controls require the same local origin")
                require(secrets.compare_digest(self.headers.get("X-CGB-Control-Token", ""), controller.token), "invalid dashboard control token")
                require(self.headers.get("Content-Type", "").split(";")[0] == "application/json", "controls require JSON")
                size = int(self.headers.get("Content-Length", "0"))
                require(0 < size <= 16384, "invalid control request size")
                self.connection.settimeout(5)
                from .jsonio import decode
                value = decode(self.rfile.read(size))
                if self.path == "/api/jobs/start":
                    result = controller.start(value)
                elif self.path == "/api/jobs/stop":
                    require(type(value) is dict and set(value) == {"id"}, "invalid stop request")
                    result = controller.stop(value["id"])
                return self.send(encoded(result), "application/json; charset=utf-8", 202)
            except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
                return self.send(encoded({"error": str(exc)}), "application/json; charset=utf-8", 400)
    return Handler


def serve_dashboard(root, port=8765):
    require(type(port) is int and 0 <= port <= 65535, "invalid dashboard port")
    root = Path(root).absolute()
    require((root / "suites/portable-evidence-v1.json").is_file(), "Select a benchmark workspace created with cgb init or a source checkout")
    from .dashboard_control import DashboardController
    controller = DashboardController(root)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler(root, controller))
    except PermissionError:
        return {"dashboard": "http-unavailable", "reason": "This host forbids local port binding.",
                "fallback": "Use cgb dashboard export for an offline HTML report."}
    print("Dashboard: http://127.0.0.1:"+str(server.server_address[1]), flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.server_close()
    return {"dashboard": "stopped"}
