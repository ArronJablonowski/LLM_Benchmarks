import json

from .delivery import deliver


def format_event(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def format_and_deliver(event: dict, channel: str) -> str:
    return deliver(channel, format_event(event))
