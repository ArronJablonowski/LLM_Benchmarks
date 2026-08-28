export class CartStore {
  constructor(_storage = null) {}
  add(_product) {}
  setQuantity(_productId, _quantity) {}
  remove(_productId) {}
  subscribe(_listener) { return () => {}; }
  snapshot() { return []; }
  subtotal() { return 0; }
}
