"""Self-contained read-only platform export; bundled code hash, no network."""

import base64
import hashlib

from .dashboard import asset
from .jsonio import encoded, write_new
from .platform import collect


def export(root, output):
    data = encoded(collect(root)).decode().replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    script = "window.CGB_PLATFORM_SNAPSHOT=" + data + ";\n" + asset("platform.js").decode()
    sha = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    html = asset("platform.html").decode().replace('<!--CONTROL-->', '')
    html = html.replace('<link rel="stylesheet" href="/platform.css">', '<style>' + asset("platform.css").decode() + '</style>')
    html = html.replace('<script src="/platform.js" defer></script>', '<script>' + script + '</script>')
    policy = "default-src 'none'; script-src 'sha256-" + sha + "'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + policy + '">')
    write_new(output, html.encode())
    return {"dashboard": str(output), "offline_snapshot": True, "controls": False}
