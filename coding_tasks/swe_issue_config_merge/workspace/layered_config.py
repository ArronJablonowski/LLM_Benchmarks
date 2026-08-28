"""Small dependency-free layered configuration helper."""


def merge(base: dict, overlay: dict) -> dict:
    """Return base with overlay values applied.

    This implementation contains the regression described in the issue.
    """
    result = base
    for key, value in overlay.items():
        if key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] += value
        else:
            result[key] = value
    return result
