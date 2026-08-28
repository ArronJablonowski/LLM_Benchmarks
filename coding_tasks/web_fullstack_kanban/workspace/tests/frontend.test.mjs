import test from "node:test";
import assert from "node:assert/strict";
import { cardPayload } from "../webboard/static/app.mjs";

test("normalizes a card creation payload", () => {
  assert.deepEqual(cardPayload("  Ship release  "), { title: "Ship release", column: "todo" });
});
