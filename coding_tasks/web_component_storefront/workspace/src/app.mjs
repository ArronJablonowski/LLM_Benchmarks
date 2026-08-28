import "./product-card.mjs";
import { CartStore } from "./cart-store.mjs";

const cart = new CartStore(globalThis.localStorage);
void cart;
// TODO: load products and implement the storefront and cart dialog.
