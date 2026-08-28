import test from "node:test";
import assert from "node:assert/strict";
import { filterIncidents, summarizeIncidents } from "../app.mjs";

const rows = [
  { id: "1", title: "API failure", service: "gateway", status: "open" },
  { id: "2", title: "Recovered", service: "search", status: "resolved" },
];

test("filters by status", () => {
  assert.deepEqual(filterIncidents(rows, { query: "", status: "open" }), [rows[0]]);
});

test("summarizes all incidents", () => {
  assert.deepEqual(summarizeIncidents(rows), { open: 1, monitoring: 0, resolved: 1, total: 2 });
});
