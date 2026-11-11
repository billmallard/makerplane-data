# SPDX-License-Identifier: Apache-2.0
# Local stand-in for navdata.aerocommons.org: site/ from the worktree, the
# catalog and packs proxied from the live origin. TAMPER=pack|manifest flips
# one byte so the refusal paths can be driven end to end.
import http.server, os, sys, urllib.request, pathlib
SITE = pathlib.Path(sys.argv[2]); ORIGIN = "https://navdata.aerocommons.org"
CACHE = pathlib.Path("cache"); CACHE.mkdir(exist_ok=True)
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(s, *a, **k): super().__init__(*a, directory=str(SITE), **k)
    def log_message(s, *a): pass
    def do_GET(s):
        path = s.path.split("?")[0]
        if path.startswith(("/manifest.json", "/packs/")):
            f = CACHE / path.strip("/").replace("/", "_")
            if not f.exists():
                f.write_bytes(urllib.request.urlopen(urllib.request.Request(ORIGIN + path, headers={"User-Agent": "curl/8.5.0"}), timeout=300).read())
            data = bytearray(f.read_bytes())
            t = os.environ.get("TAMPER", "")
            if (t == "pack" and path.startswith("/packs/")) or (t == "manifest" and path == "/manifest.json"):
                data[len(data) // 2] ^= 1
            s.send_response(200); s.send_header("Content-Length", str(len(data))); s.end_headers()
            s.wfile.write(data); return
        return super().do_GET()
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
