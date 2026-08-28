# WebBoard specification

Implement a persistent three-column board (`todo`, `doing`, `done`).

## Python API

Export `BoardStore`, `Conflict`, and `create_app` from `webboard`.

- `BoardStore(database_path).create(title, column="todo")` creates and returns
  `{id, title, column, version}` with version 1.
- `list()` returns cards ordered by creation ID.
- `update(card_id, *, title=None, column=None, expected_version)` performs an
  optimistic-concurrency update, increments the version, and raises `Conflict`
  if the expected version is stale.
- Titles are trimmed, non-empty, and at most 120 characters. Columns are limited
  to the three values above. Use parameterized SQL and transactions.

`create_app(database_path, static_directory)` returns a WSGI application:

- `GET /api/cards` → `200` JSON array.
- `POST /api/cards` → `201` JSON card and a quoted numeric `ETag`.
- `PATCH /api/cards/{id}` requires `If-Match`; success → `200` and the new
  `ETag`, stale versions → `412`, invalid input → `400`, missing cards → `404`.
- `GET /` and safe files below the static directory are served with correct
  content types. Traversal outside that directory is never served.

## Frontend

Build a responsive semantic board using the API. Include labeled card creation,
keyboard-operable move controls, loading/empty/error states, visible focus,
live announcements, and safe DOM construction without `innerHTML`. Handle a
`412` by refreshing and explaining the conflict. Use ES modules and add tests.
