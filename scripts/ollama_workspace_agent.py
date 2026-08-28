#!/usr/bin/env python3
"""Run a bounded workspace-editing tool loop directly against Ollama."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOOL_OUTPUT = 24_000
ALLOWED_COMMANDS = {"git", "node", "npm", "python", "python3", "pytest"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--max-turns", type=int, default=150)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    return parser.parse_args(argv)


def tools() -> list[dict]:
    def tool(name, description, properties, required):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object", "properties": properties,
                    "required": required, "additionalProperties": False,
                },
            },
        }

    return [
        tool("list_files", "List workspace files recursively.", {}, []),
        tool("read_file", "Read a UTF-8 workspace file.", {
            "path": {"type": "string"},
        }, ["path"]),
        tool("write_file", "Create or replace a UTF-8 workspace file.", {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, ["path", "content"]),
        tool("replace_text", "Replace one exact occurrence in a workspace file.", {
            "path": {"type": "string"}, "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        }, ["path", "old_text", "new_text"]),
        tool("remove_file", "Remove one workspace file.", {
            "path": {"type": "string"},
        }, ["path"]),
        tool("run_command", "Run a bounded, non-shell development command in the workspace.", {
            "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "cwd": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 180},
        }, ["argv"]),
    ]


class WorkspaceTools:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)

    def path(self, value: str, *, must_exist: bool = False) -> Path:
        candidate = (self.root / value).resolve(strict=False)
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("path escapes the benchmark workspace")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(value)
        return candidate

    def call(self, name: str, arguments: dict) -> str:
        if name == "list_files":
            ignored = {".git", ".next", "node_modules", "__pycache__"}
            values = [
                str(path.relative_to(self.root))
                for path in self.root.rglob("*")
                if path.is_file() and not any(part in ignored for part in path.relative_to(self.root).parts)
            ]
            return "\n".join(sorted(values))[:MAX_TOOL_OUTPUT]
        if name == "read_file":
            path = self.path(arguments["path"], must_exist=True)
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("file is not a readable bounded regular file")
            return path.read_text(encoding="utf-8")[:MAX_TOOL_OUTPUT]
        if name == "write_file":
            content = arguments["content"]
            if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise ValueError("file exceeds the 2 MiB tool limit")
            path = self.path(arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"wrote {path.relative_to(self.root)}"
        if name == "replace_text":
            path = self.path(arguments["path"], must_exist=True)
            content = path.read_text(encoding="utf-8")
            old = arguments["old_text"]
            if not old or content.count(old) != 1:
                raise ValueError("old_text must match exactly once")
            updated = content.replace(old, arguments["new_text"], 1)
            if len(updated.encode("utf-8")) > MAX_FILE_BYTES:
                raise ValueError("updated file exceeds the 2 MiB tool limit")
            path.write_text(updated, encoding="utf-8")
            return f"updated {path.relative_to(self.root)}"
        if name == "remove_file":
            path = self.path(arguments["path"], must_exist=True)
            if not path.is_file():
                raise ValueError("remove_file accepts regular files only")
            path.unlink()
            return f"removed {path.relative_to(self.root)}"
        if name == "run_command":
            argv = arguments["argv"]
            if not argv or argv[0] not in ALLOWED_COMMANDS:
                raise ValueError("command is outside the frozen development allowlist")
            cwd = self.path(arguments.get("cwd", "."), must_exist=True)
            if not cwd.is_dir():
                raise ValueError("cwd is not a directory")
            timeout = min(int(arguments.get("timeout", 120)), 180)
            env = {
                **os.environ, "NO_PROXY": "*",
                "PIP_NO_INDEX": "1", "npm_config_offline": "true",
                "PYTHONDONTWRITEBYTECODE": "1",
                "XDG_CONFIG_HOME": str(self.root.parent / ".benchmark-config"),
                "XDG_CACHE_HOME": str(self.root.parent / ".benchmark-cache"),
            }
            proc = subprocess.run(
                argv, cwd=cwd, env=env, text=True, capture_output=True,
                timeout=timeout, check=False,
            )
            return json.dumps({
                "exit_code": proc.returncode,
                "stdout": proc.stdout[-MAX_TOOL_OUTPUT // 2:],
                "stderr": proc.stderr[-MAX_TOOL_OUTPUT // 2:],
            })
        raise ValueError(f"unknown tool: {name}")


def request(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=max(1, timeout)) as response:
        return json.load(response)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not 1 <= args.timeout <= 14_400 or not 1 <= args.max_turns <= 500:
        raise SystemExit("invalid timeout or max-turns")
    workspace = WorkspaceTools(args.workspace)
    messages = [{
        "role": "system",
        "content": (
            "You are a benchmark coding/design agent. Work autonomously only inside the supplied "
            "workspace using the provided tools. Do not access the network, secrets, user files, or "
            "paths outside the workspace. Inspect existing files, implement the request completely, "
            "run relevant offline tests when useful, and finish with a concise summary."
        ),
    }, {"role": "user", "content": args.prompt}]
    deadline = time.monotonic() + args.timeout
    for _turn in range(args.max_turns):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("workspace agent exhausted its run budget")
        data = request(args.ollama_url, {
            "model": args.model, "messages": messages, "tools": tools(),
            "stream": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": -1},
        }, min(remaining, 1800))
        message = data.get("message") or {}
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            print(message.get("content") or "")
            return 0
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            try:
                result = workspace.call(name, arguments)
            except Exception as exc:  # Give bounded tool errors back to the model.
                result = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
            messages.append({"role": "tool", "tool_name": name, "content": result})
    raise RuntimeError("workspace agent exceeded the maximum tool-call turns")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TimeoutError as exc:
        print(f"TIMEOUT: {exc}", file=sys.stderr)
        raise SystemExit(124)
