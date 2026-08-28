# Incident dashboard

Finish the accessible incident dashboard without adding runtime dependencies.

`app.mjs` must export:

- `filterIncidents(incidents, { query, status })`
- `summarizeIncidents(incidents)` returning counts for `open`, `monitoring`,
  `resolved`, and `total`
- `normalizeFilters(searchParams)` returning validated `{ query, status }`

Run JavaScript tests with `node --test tests/*.test.mjs`. Serve the directory
with any static HTTP server to inspect the application in a browser.
