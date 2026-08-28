import test from "node:test";
import assert from "node:assert/strict";
import { CartStore } from "../src/cart-store.mjs";

test("adds products and calculates a cent-accurate subtotal", () => {
  const cart = new CartStore();
  cart.add({ id: "lamp", name: "Lamp", priceCents: 1999 });
  cart.setQuantity("lamp", 2);
  assert.equal(cart.subtotal(), 3998);
  assert.equal(cart.snapshot()[0].quantity, 2);
});

test("rejects invalid quantities", () => {
  const cart = new CartStore();
  cart.add({ id: "lamp", name: "Lamp", priceCents: 1999 });
  assert.throws(() => cart.setQuantity("lamp", -1));
});
