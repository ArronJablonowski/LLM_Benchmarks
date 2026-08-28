from .formatting import format_event


def deliver(channel: str, payload: str) -> str:
    if channel == "stdout":
        return f"OUT:{payload}"
    if channel == "audit":
        return f"AUDIT:{payload}"
    raise KeyError(channel)


def deliver_event(channel: str, event: dict) -> str:
    return deliver(channel, format_event(event))
