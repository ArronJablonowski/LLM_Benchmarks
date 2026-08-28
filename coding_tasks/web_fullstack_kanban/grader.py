from __future__ import annotations

import ast
import io
import json
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from wsgiref.util import setup_testing_defaults

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_support import Checks, count_web_tests, python_files_parse, run_node_tests, run_project_tests


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(); self.tags = []; self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag); self.attrs.append((tag, dict(attrs)))


def wsgi_request(app, method, path, payload=None, headers=None):
    body = b"" if payload is None else json.dumps(payload).encode()
    environ = {}; setup_testing_defaults(environ)
    environ.update({"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/json", "wsgi.input": io.BytesIO(body)})
    for name, value in (headers or {}).items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value
    captured = {}
    def start_response(status, response_headers):
        captured["status"] = int(status.split()[0]); captured["headers"] = dict(response_headers)
    response = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], response


def main(workspace: Path) -> int:
    checks = Checks(); sys.path.insert(0, str(workspace))

    def persistence_and_conflicts():
        from webboard import BoardStore, Conflict
        with tempfile.TemporaryDirectory() as directory:
            store = BoardStore(Path(directory) / "board.db")
            first = store.create("  Ship release  ")
            assert first["title"] == "Ship release" and first["column"] == "todo" and first["version"] == 1
            changed = store.update(first["id"], column="doing", expected_version=1)
            assert changed["column"] == "doing" and changed["version"] == 2
            try: store.update(first["id"], column="done", expected_version=1)
            except Conflict: pass
            else: raise AssertionError("stale update was accepted")
            assert store.list() == [changed]

    checks.call("persistent store and optimistic concurrency", persistence_and_conflicts)

    def http_contract():
        from webboard import create_app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); static = root / "static"; static.mkdir(); (static / "index.html").write_text("ok", encoding="utf-8")
            app = create_app(root / "board.db", static)
            status, headers, body = wsgi_request(app, "POST", "/api/cards", {"title": "Card"})
            assert status == 201 and headers.get("ETag") == '"1"'
            card = json.loads(body); card_id = card["id"]
            status, headers, body = wsgi_request(app, "PATCH", f"/api/cards/{card_id}", {"column": "doing"}, {"If-Match": '"1"'})
            assert status == 200 and headers.get("ETag") == '"2"' and json.loads(body)["column"] == "doing"
            assert wsgi_request(app, "PATCH", f"/api/cards/{card_id}", {"column": "done"}, {"If-Match": '"1"'})[0] == 412
            assert wsgi_request(app, "GET", "/api/cards")[0] == 200
            assert wsgi_request(app, "GET", "/../SPEC.md")[0] in {400, 403, 404}

    checks.call("WSGI JSON, ETag, and safe-static contract", http_contract)
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (workspace / "webboard").glob("*.py"))
    tree = ast.parse(sources)
    unsafe_sql = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany"} and node.args and isinstance(node.args[0], (ast.JoinedStr, ast.BinOp))]
    checks.check("SQL statements are parameterized", not unsafe_sql)
    static = workspace / "webboard" / "static"; index = (static / "index.html").read_text(encoding="utf-8"); css = (static / "styles.css").read_text(encoding="utf-8").lower(); js = (static / "app.mjs").read_text(encoding="utf-8")
    markup = Markup(); markup.feed(index)
    checks.check("accessible responsive frontend", any(tag == "html" and row.get("lang") for tag, row in markup.attrs) and "main" in markup.tags and any(row.get("aria-live") for _tag, row in markup.attrs) and "@media" in css and ":focus-visible" in css)
    checks.check("safe frontend with complete states", "innerhtml" not in js.lower() and "textContent" in js and all(word in js.lower() for word in ("loading", "empty", "error", "412")))
    ok, detail = run_project_tests(workspace); checks.check("backend tests pass", ok, detail)
    ok, detail = run_node_tests(workspace); checks.check("frontend tests pass", ok, detail)
    checks.check("student expanded backend and frontend tests", len(list((workspace / "tests").glob("test*.py"))) >= 2 and count_web_tests(workspace) >= 2)
    ok, detail = python_files_parse(workspace); checks.check("all Python parses", ok, detail)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
