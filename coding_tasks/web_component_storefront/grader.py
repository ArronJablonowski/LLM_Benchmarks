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
    index = (workspace / "index.html").read_text(encoding="utf-8")
    css = (workspace / "styles.css").read_text(encoding="utf-8").lower()
    app = (workspace / "src" / "app.mjs").read_text(encoding="utf-8")
    card = (workspace / "src" / "product-card.mjs").read_text(encoding="utf-8")
    store = (workspace / "src" / "cart-store.mjs").read_text(encoding="utf-8")
    markup = Markup(); markup.feed(index)
    checks.check("semantic responsive shell", any(tag == "html" and row.get("lang") for tag, row in markup.attrs) and "main" in markup.tags and "@media" in css and re.search(r"display\s*:\s*(grid|flex)", css) is not None)
    checks.check("accessible native cart dialog", "dialog" in markup.tags and any(tag == "button" and row.get("type") == "button" for tag, row in markup.attrs) and ("aria-live" in index or "role=\"status\"" in index))
    checks.check("encapsulated custom element", "customElements.define" in card and "attachShadow" in card and "dispatchEvent" in card)
    combined = app + card
    checks.check("safe DOM construction", "innerhtml" not in combined.lower() and "textContent" in combined and "createElement" in combined)
    checks.check("loading, empty, and error states", all(word in app.lower() for word in ("loading", "empty", "error")) and "fetch(" in app)
    checks.check("state persistence and immutable snapshots", "localStorage" in app and ("structuredClone" in store or "Object.freeze" in store or ".map(" in store))
    ok, detail = run_node_tests(workspace); checks.check("JavaScript tests pass", ok, detail)
    checks.check("student expanded JavaScript tests", count_web_tests(workspace) >= 2)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
