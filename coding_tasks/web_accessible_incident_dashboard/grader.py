from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_support import Checks, count_web_tests, run_node_tests


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(); self.tags = []; self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag); self.attrs.append((tag, dict(attrs)))


def main(workspace: Path) -> int:
    checks = Checks()
    html_path = workspace / "index.html"; css_path = workspace / "styles.css"; js_path = workspace / "app.mjs"
    markup = Markup(); markup.feed(html_path.read_text(encoding="utf-8"))
    css = css_path.read_text(encoding="utf-8").lower(); source = js_path.read_text(encoding="utf-8")
    attrs = markup.attrs
    checks.check("document metadata and landmarks", any(tag == "html" and row.get("lang") for tag, row in attrs) and any(tag == "meta" and row.get("name") == "viewport" for tag, row in attrs) and "main" in markup.tags)
    checks.check("skip link and live status", any(tag == "a" and row.get("href", "").startswith("#") and "skip" in " ".join(row.values()).lower() for tag, row in attrs) and any(row.get("aria-live") in {"polite", "assertive"} for _tag, row in attrs))
    input_ids = {row.get("id") for tag, row in attrs if tag in {"input", "select"} and row.get("id")}
    labels = {row.get("for") for tag, row in attrs if tag == "label" and row.get("for")}
    checks.check("filters have associated labels", bool(input_ids) and input_ids <= labels)
    checks.check("responsive and accessible CSS", "@media" in css and ("max-width" in css or "min-width" in css) and ":focus-visible" in css and "prefers-reduced-motion" in css and re.search(r"--[a-z0-9-]+\s*:", css) is not None)
    checks.check("safe browser rendering and theme persistence", "innerhtml" not in source.lower() and "textContent" in source and "localStorage" in source and "matchMedia" in source)
    ok, detail = run_node_tests(workspace); checks.check("JavaScript tests pass", ok, detail)
    checks.check("student expanded JavaScript tests", count_web_tests(workspace) >= 2)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
