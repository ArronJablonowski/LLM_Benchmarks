from __future__ import annotations

import argparse
from pathlib import Path
from wsgiref.simple_server import make_server

from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("board.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    static = Path(__file__).with_name("static")
    with make_server(args.host, args.port, create_app(args.database, static)) as server:
        print(f"WebBoard listening on http://{args.host}:{args.port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
