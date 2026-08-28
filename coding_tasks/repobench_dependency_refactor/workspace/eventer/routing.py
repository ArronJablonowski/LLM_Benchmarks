from .formatting import format_and_deliver


def send_event(event: dict, channel: str = "stdout") -> str:
    return format_and_deliver(event, channel)
