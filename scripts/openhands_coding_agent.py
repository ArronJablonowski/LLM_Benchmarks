#!/usr/bin/env python3
"""Run one isolated coding task through the OpenHands SDK."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from openhands.sdk import Agent, Conversation, LLM, Tool  # noqa: E402
from openhands.tools.file_editor import FileEditorTool  # noqa: E402
from openhands.tools.task_tracker import TaskTrackerTool  # noqa: E402
from openhands.tools.terminal import TerminalTool  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--api-key", default="ollama")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args()
    llm = LLM(
        model=f"openai/{args.model}", api_key=args.api_key,
        base_url=args.base_url, temperature=0,
        reasoning_effort=None, native_tool_calling=True,
        max_output_tokens=16384, num_retries=0, timeout=1800,
    )
    agent = Agent(llm=llm, tools=[
        Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ])
    conversation = Conversation(
        agent=agent, workspace=args.workspace, max_iteration_per_run=150,
        stuck_detection=True, visualizer=None,
    )
    try:
        conversation.send_message(args.prompt)
        conversation.run()
    finally:
        conversation.close()
    print("OpenHands coding task finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
