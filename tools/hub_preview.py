"""Serve hub_page.html com dados AO VIVO do hub real (8770) sem reinjetar o pyc.

    python tools/hub_preview.py            # http://127.0.0.1:8791

Leitura (/stream, /api/state, /api/structure, /api/outputs) e repassada ao 8770.
Escrita (/api/cmd, /api/set, ...) NAO chega no motor: responde {} e loga no console.
Serve p/ ver o painel novo com musica tocando antes de `inject_page.py`.
"""
import http.server, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "hub_page.html"
UP = "http://127.0.0.1:8770"
READ = ("/stream", "/api/state", "/api/structure", "/api/outputs")


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            body = HTML.read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if path in READ:
            try:
                r = urllib.request.urlopen(UP + self.path, timeout=10)
            except Exception as e:
                self.send_error(502, str(e)); return
            self.send_response(200); self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
            self.send_header("Cache-Control", "no-cache"); self.end_headers()
            try:
                if path == "/stream":
                    while True:
                        chunk = r.readline()
                        if not chunk:
                            break
                        self.wfile.write(chunk); self.wfile.flush()
                else:
                    self.wfile.write(r.read())
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                pass
            return
        print("[preview] bloqueado (escrita):", self.path, flush=True)
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b"{}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    print(f"[preview] http://127.0.0.1:{port}  (leitura via {UP}, escrita bloqueada)", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
