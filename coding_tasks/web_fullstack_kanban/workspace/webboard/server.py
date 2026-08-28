from __future__ import annotations


class Conflict(RuntimeError):
    pass


class BoardStore:
    def __init__(self, database_path):
        self.database_path = database_path

    def create(self, title, column="todo"):
        raise NotImplementedError

    def list(self):
        return []

    def update(self, card_id, *, title=None, column=None, expected_version):
        raise NotImplementedError


def create_app(database_path, static_directory):
    raise NotImplementedError
