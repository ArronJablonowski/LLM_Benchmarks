"""Streaming normalization pipeline."""


def process(records, secret_fields=frozenset({"password", "token"})):
    raise NotImplementedError
