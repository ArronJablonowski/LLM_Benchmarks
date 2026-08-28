# Component storefront

Complete this dependency-free ES-module storefront. It must run from a static
HTTP server without compilation.

`src/cart-store.mjs` must export `CartStore`. Its constructor accepts an
optional storage-compatible object. Required methods are `add(product)`,
`setQuantity(productId, quantity)`, `remove(productId)`, `subscribe(listener)`,
`snapshot()`, and `subtotal()`. Snapshots must not expose mutable internal
state, invalid quantities must throw, subscribers receive new snapshots, and
the returned subscription cleanup must work.

Run tests with `node --test tests/*.test.mjs`.
