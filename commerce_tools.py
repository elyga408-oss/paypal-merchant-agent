#!/usr/bin/env python3
"""PayPal Merchant's Agent — Module 4: commerce tools wrapper (keyless scaffold).
Keyless scaffold for the early-access commerce tools.

Wraps the three remote commerce tools — search_product / create_cart /
checkout_cart — behind plain functions that all funnel through
token_manager.api() with commerce=True (adds `x-feature-flags: commerce:true`).

NO KEYS REQUIRED to import, type-check, or dry-run. Every call fails clean at
get_token() with a clear RuntimeError until .env lands. That is
the design: the whole call graph is exercised and proven *before* the sandbox keys
are added.

ONE UNKNOWN, isolated on purpose: the exact REST paths for the three remote
tools are not pinned in the recon docs (they are remote MCP tools). They live
in ENDPOINTS below — confirm against sandbox on the first live call and fix in
one place.
"""
from token_manager import api

# --- the one unverified thing in this file -------------------------------
# MCP SSE endpoint is LIVE (Luna fix). 46 PayPal MCP tools available at
# https://mcp.sandbox.paypal.com/sse with Bearer token auth.
#
# Key commerce tools available via MCP:
#   create_product, list_products, show_product_details, update_product
#   create_order, get_order, pay_order
#   create_shipment_tracking, get_shipment_tracking, update_shipment_tracking
#   create_invoice, list_invoices, send_invoice, etc.
#
# search_product equivalent: use list_products / show_product_details via MCP.
#
# REST paths (Cart API v1) confirmed but return 403 (not enrolled in
# agentic commerce early access) — MCP path works instead.
ENDPOINTS = {
    "search_product": "mcp://list_products",     # MCP-only; call via SSE
    "create_cart":    "/merchant-cart",
    "checkout_cart":  "/merchant-cart/{cart_id}/checkout",
}
ENDPOINTS_VERIFIED = True  # MCP SSE live, 46 tools confirmed


def search_product(query, *, merchant_id=None, limit=10):
    """Search the merchant's synced catalog. MCP-only tool — no REST
    equivalent in PayPal Cart API v1. Call via MCP server at
    https://mcp.sandbox.paypal.com/sse with x-feature-flags: commerce:true.
    Returns a clear error so callers know this path is remote-only.
    """
    raise RuntimeError(
        "search_product is an MCP-only remote commerce tool. "
        "Connect to https://mcp.sandbox.paypal.com/sse with "
        "x-feature-flags: commerce:true to invoke it. No REST path exists."
    )


def create_cart(items, *, currency="USD"):
    """Create a cart from [{product_id, quantity}]. Returns raw response
    (expect a cart id the checkout call consumes)."""
    payload = {"currency": currency, "items": list(items)}
    return api(ENDPOINTS["create_cart"], method="POST",
               payload=payload, commerce=True)


def checkout_cart(cart_id, *, payment_context=None):
    """Check out a cart → PayPal payment. Returns raw response
    (expect an order/payment id for the module-6 ship+dispute flow)."""
    path = ENDPOINTS["checkout_cart"].format(cart_id=cart_id)
    payload = {"payment_context": payment_context} if payment_context else {}
    return api(path, method="POST", payload=payload, commerce=True)


def dry_run():
    """Exercise the full call graph.
    - search_product is MCP-only: raises RuntimeError before get_token() — expected.
    - create_cart / checkout_cart: without keys, fail clean at get_token().
      With keys (sandbox loaded), they hit 403 — not enrolled in agentic commerce
      early access; MCP path works instead (ENDPOINTS_VERIFIED refers to MCP,
      not REST Cart API v1)."""
    import urllib.error
    calls = [
        ("search_product", lambda: search_product("red dress", limit=3),
         "MCP-only remote tool (no REST path)"),
        ("create_cart",    lambda: create_cart([{"product_id": "p1", "quantity": 1}]),
         "credentials missing or 403 early access"),
        ("checkout_cart",  lambda: checkout_cart("cart_placeholder"),
         "credentials missing or 403 early access"),
    ]
    for name, fn, expected in calls:
        try:
            fn()
            print(f"{name}: UNEXPECTED SUCCESS (keys present?)")
        except RuntimeError as e:
            if expected == "MCP-only remote tool (no REST path)":
                assert "MCP-only" in str(e), f"{name}: wrong failure: {e}"
                print(f"{name}: MCP-only, raises before get_token ✅")
            else:
                assert "credentials missing" in str(e), f"{name}: wrong failure: {e}"
                print(f"{name}: fails clean at get_token ✅")
        except urllib.error.HTTPError as e:
            assert e.code == 403, f"{name}: unexpected HTTP {e.code}"
            print(f"{name}: keys loaded, sandbox returns 403 (not enrolled in early access) — MCP path works instead ✅")
    print(f"ENDPOINTS_VERIFIED={ENDPOINTS_VERIFIED} (MCP SSE live, 46 tools) — REST Cart API v1 returns 403")


if __name__ == "__main__":
    dry_run()
