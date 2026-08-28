#!/usr/bin/env python3
"""Run one prompt through the OpenHands SDK with tools disabled."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from openhands.sdk import Agent, Conversation, LLM  # noqa: E402
from openhands.sdk.event import ActionEvent, MessageEvent  # noqa: E402
from openhands.sdk.tool.builtins.finish import FinishAction  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    replies: list[str] = []

    def capture(event):
        # OpenHands completes ordinary no-tools prompts with FinishAction rather
        # than an agent MessageEvent.  Capture both representations so SDK
        # version changes do not turn valid completions into transport errors.
        if isinstance(event, ActionEvent) and isinstance(event.action, FinishAction):
            text = event.action.message.strip()
            if text:
                replies.append(text)
            return
        if not isinstance(event, MessageEvent) or event.source != "agent":
            return
        texts = [getattr(item, "text", "") for item in event.llm_message.content]
        text = "\n".join(part for part in texts if part).strip()
        if text:
            replies.append(text)

    llm = LLM(
        model=f"openai/{args.model}",
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        temperature=0,
        reasoning_effort=None,
        native_tool_calling=False,
        max_output_tokens=4096,
        num_retries=0,
        timeout=1800,
    )
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(
        agent=agent,
        workspace=args.workspace,
        callbacks=[capture],
        max_iteration_per_run=1,
        stuck_detection=False,
        visualizer=None,
    )
    try:
        conversation.send_message(args.prompt)
        conversation.run()
    finally:
        conversation.close()
    if not replies:
        raise RuntimeError("OpenHands produced no assistant message")
    print(replies[-1])


if __name__ == "__main__":
    main()
