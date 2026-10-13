"""Zero-dependency web server for the playground:  python -m explainparse.server [--port 8000]"""
import argparse
import glob
import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from .engine import analyze

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web", "index.html")
EXAMPLES = os.path.join(ROOT, "examples")
MAX_SOURCE = 100_000

TITLES = {"missing_brace_root_cause": "Missing } found by indentation", "keyword_typo": "Misspelled keywords",
          "many_errors": "Several errors at once", "unclosed_paren": "Unclosed parenthesis",
          "missing_semicolon": "Missing semicolon", "lexical": "Lexical errors", "valid": "A valid program"}


def examples():
    out = []
    for f in sorted(glob.glob(os.path.join(EXAMPLES, "*.lm"))):
        name = os.path.splitext(os.path.basename(f))[0]
        out.append({"name": name, "title": TITLES.get(name, name.replace("_", " ")),
                    "source": open(f, encoding="utf-8").read()})
    return out


def handle_analyze(body: bytes):
    try:
        data = json.loads(body or b"{}")
        source = data.get("source", "")
        if not isinstance(source, str):
            raise ValueError
    except (ValueError, AttributeError):
        return 400, {"error": "expected JSON body {\"source\": \"...\"}"}
    if len(source) > MAX_SOURCE:
        return 413, {"error": f"source is limited to {MAX_SOURCE} characters"}
    return 200, analyze(source).to_dict()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, open(WEB, "rb").read(), "text/html; charset=utf-8")
        elif self.path == "/api/examples":
            self._send(200, examples())
        elif self.path == "/api/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/analyze":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        code, payload = handle_analyze(self.rfile.read(min(n, MAX_SOURCE * 4)))
        self._send(code, payload)

    def log_message(self, fmt, *args):
        pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    print(f"ExplainParse playground running at http://{a.host}:{a.port}")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
