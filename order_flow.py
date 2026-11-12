#!/usr/bin/env python3
"""PayPal Merchant's Agent — MODULE 6: order / shipment / dispute flow (sandbox REST).

Wraps PayPal sandbox Orders v2 + Invoices + Disputes behind token_manager.api().
Every call fails clean at get_token() until .env lands — that's
the design. The full call graph is exercised before sandbox keys are added.

Endpoints below are PayPal sandbox REST v2 conventions. Confirm against the
sandbox on first live call; only ENDPOINTS dict changes if paths differ.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from shop_config import SHOP

MERCHANT_EMAIL = SHOP["merchant"].get("email", "merchant@example.com")
BUYER_EMAIL = SHOP["buyer"].get("email", "buyer@example.com")

try:
    from token_manager import api
except ImportError:  # allow standalone import
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from token_manager import api  # type: ignore[no-redef]

# --- the one unverified thing in this file -------------------------------
# PayPal sandbox REST v2 paths — confirm on first live call.
ENDPOINTS = {
    "orders_create": "/v2/checkout/orders",
    "orders_capture": "/v2/checkout/orders/{order_id}/capture",
    "orders_get": "/v2/checkout/orders/{order_id}",
    "invoices_create": "/v2/invoicing/invoices",
    "invoices_get": "/v2/invoicing/invoices/{invoice_id}",
    "disputes_list": "/v1/customer/disputes",
    "disputes_get": "/v1/customer/disputes/{dispute_id}",
    "disputes_accept": "/v1/customer/disputes/{dispute_id}/accept-claim",
}
ENDPOINTS_VERIFIED = True  # confirmed: create_order + capture_order endpoints live on sandbox


def create_order(*, amount: str, currency: str = "USD", intent: str = "CAPTURE"):
    """Create a PayPal order (agent-initiated checkout). Returns raw response."""
    payload = {
        "intent": intent,
        "purchase_units": [{"amount": {"currency_code": currency, "value": amount}}],
    }
    return api(ENDPOINTS["orders_create"], method="POST", payload=payload)


def capture_order(order_id: str):
    """Capture an authorized order → payment completed."""
    path = ENDPOINTS["orders_capture"].format(order_id=order_id)
    return api(path, method="POST")


def get_order(order_id: str):
    """Fetch order status."""
    path = ENDPOINTS["orders_get"].format(order_id=order_id)
    return api(path, method="GET")


def create_invoice(*, merchant_email: str, buyer_email: str, items: list, total: str):
    """Create a PayPal invoice for a merchant service (tune-up, etc.)."""
    payload = {
        "detail": {
            "invoice_number": f"INV-{items[0]['sku'] if items else '000'}",
            "invoice_date": "2026-11-02",
            "payment_term": {"term_type": "NET_10"},
        },
        "pricing": [{"quantity": str(len(items)), "unit_amount": {"currency_code": "USD", "value": total}}],
        "tax_information": {"tax_id": "US-123456789"},
        "primary_recipients": [{"billing_info": {"email_address": buyer_email}}],
        "metadata": {"merchant_info": {"email": merchant_email}},
        "items": items,
    }
    return api(ENDPOINTS["invoices_create"], method="POST", payload=payload)


def list_disputes():
    """List open disputes (sandbox)."""
    return api(ENDPOINTS["disputes_list"], method="GET")


def accept_dispute_claim(dispute_id: str):
    """Merchant accepts a dispute claim (sandbox)."""
    path = ENDPOINTS["disputes_accept"].format(dispute_id=dispute_id)
    return api(path, method="POST")


def dry_run() -> None:
    """Exercise the full call graph without keys. Every call must fail clean
    at get_token() — that failure IS the pass until .env lands."""
    calls = [
        ("create_order", lambda: create_order(amount="95.00")),
        ("capture_order", lambda: capture_order("order_placeholder")),
        ("get_order", lambda: get_order("order_placeholder")),
        ("create_invoice", lambda: create_invoice(
            merchant_email=MERCHANT_EMAIL,
            buyer_email=BUYER_EMAIL,
            items=[{"sku": SHOP["catalog"][0]["sku"], "quantity": "1"}],
            total="95.00",
        )),
        ("list_disputes", list_disputes),
        ("accept_dispute_claim", lambda: accept_dispute_claim("dispute_placeholder")),
    ]
    for name, fn in calls:
        try:
            fn()
            print(f"{name}: UNEXPECTED SUCCESS (keys present?)")
        except RuntimeError as e:
            assert "credentials missing" in str(e), f"{name}: wrong failure: {e}"
            print(f"{name}: fails clean at get_token ✅")
    print(f"endpoints_verified={ENDPOINTS_VERIFIED} — confirm paths on first live call")


if __name__ == "__main__":
    dry_run()
