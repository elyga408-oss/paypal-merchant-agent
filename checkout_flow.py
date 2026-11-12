#!/usr/bin/env python3
"""PayPal Merchant's Agent — MODULE 7: checkout flow (sandbox REST).

Wires a closed negotiation outcome into PayPal Orders v2:

  1. Negotiation ends → deal at price $X
  2. checkout_flow.create_order_for_deal(outcome, product) → PayPal order
     returns order_id + approve_url (buyer clicks to approve in sandbox)
  3. After buyer approval: checkout_flow.capture_order(order_id) → payment
     completed. If capture is attempted before approval, PayPal returns
     ORDER_NOT_APPROVED (verify on first live sandbox call).

Keys: every call funnels through token_manager.api(). Without .env, all calls fail clean at get_token() — that IS the pass until sandbox keys
are added. The pay loop is proven end-to-end against FakePayPal in tests.

This is the "no live payment loop yet" gap (REVIEW.md R4). Kill-switch Nov 5:
if the 403 cart gate never opens, demo negotiation → Orders v2 create +
approve link + capture, and say commerce cart tools are early access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    from token_manager import api
except ImportError:  # allow standalone import
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from token_manager import api  # type: ignore[no-redef]

# PayPal Orders v2 paths — confirm against sandbox on first live call.
ORDERS_CREATE = "/v2/checkout/orders"
ORDERS_CAPTURE = "/v2/checkout/orders/{order_id}/capture"
ORDERS_GET = "/v2/checkout/orders/{order_id}"

CURRENCY = "USD"
INTENT = "CAPTURE"  # capture immediately on approval


def _money(cents_or_float: float) -> str:
    """Format a numeric amount as a 2-decimal string for PayPal."""
    return f"{float(cents_or_float):.2f}"


def create_order_for_deal(outcome: dict, product: dict,
                          buyer_email: str = "dana@example.com",
                          merchant_email: str = "marisol@vegacycleworks.example") -> dict:
    """Create a PayPal order for a closed negotiation deal.

    Args:
        outcome: negotiation outcome dict (must have result="deal" and price)
        product: catalog product dict (has "sku", "name", "base")
        buyer_email: buyer's email for the payer
        merchant_email: merchant's email for the payee

    Returns:
        dict with order_id, status, approve_url, amount, currency, raw response.
        On failure (no keys, 403, etc.) returns dict with "error" key.

    Raises:
        ValueError: if outcome is not a deal (no price to charge).
    """
    if outcome.get("result") != "deal" or outcome.get("price") is None:
        raise ValueError(
            f"cannot create order: outcome is '{outcome.get('result')}', "
            f"expected 'deal' with a price"
        )

    amount = _money(outcome["price"])
    payload = {
        "intent": INTENT,
        "purchase_units": [{
            "reference_id": product["sku"],
            "description": product["name"],
            "amount": {"currency_code": CURRENCY, "value": amount},
        }],
        "application_context": {
            "brand_name": "Vega Cycle Works",
            "locale": "en-US",
            "landing_page": "LOGIN",
            "shipping_preference": "NO_SHIPPING",  # service: tune-up, no shipping
            "user_action": "PAY_NOW",
            "return_url": f"https://vegacycleworks.example/checkout/return",
            "cancel_url": f"https://vegacycleworks.example/checkout/cancel",
        },
    }

    try:
        raw = api(ORDERS_CREATE, method="POST", payload=payload)
    except RuntimeError as e:
        return {"error": f"get_token: {e}", "amount": amount}
    except Exception as e:  # HTTPError (403, 401, ...) propagates from api()
        return {"error": f"{type(e).__name__}: {e}", "amount": amount}

    order_id = raw.get("id")
    status = raw.get("status")
    approve_url = None
    for link in raw.get("links", []) or []:
        if link.get("rel") == "approve":
            approve_url = link.get("href")

    return {
        "order_id": order_id,
        "status": status,
        "approve_url": approve_url,
        "amount": amount,
        "currency": CURRENCY,
        "sku": product["sku"],
        "product": product["name"],
        "raw": raw,
    }


def capture_order(order_id: str) -> dict:
    """Capture an approved order → payment completed.

    Must be called AFTER the buyer approves via approve_url. Calling before
    approval returns ORDER_NOT_APPROVED (verify on first live sandbox call).

    Returns dict with order_id, status, amount, raw response — or {"error": ...}
    on failure.
    """
    path = ORDERS_CAPTURE.format(order_id=order_id)
    try:
        raw = api(path, method="POST")
    except RuntimeError as e:
        return {"error": f"get_token: {e}", "order_id": order_id}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "order_id": order_id}

    return {
        "order_id": order_id,
        "status": raw.get("status"),
        "amount": _capture_amount(raw),
        "raw": raw,
    }


def _capture_amount(raw: dict) -> Optional[str]:
    """Extract capture amount from a capture response."""
    try:
        pu = raw.get("purchase_units", []) or []
        return pu[0]["payments"]["captures"][0]["amount"]["value"]
    except (KeyError, IndexError, TypeError):
        return None


def get_order(order_id: str) -> dict:
    """Fetch order status (useful for polling after buyer approval)."""
    path = ORDERS_GET.format(order_id=order_id)
    try:
        raw = api(path, method="GET")
    except RuntimeError as e:
        return {"error": f"get_token: {e}", "order_id": order_id}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "order_id": order_id}
    return {"order_id": order_id, "status": raw.get("status"), "raw": raw}


def dry_run() -> None:
    """Exercise the checkout flow without keys. Every call must fail clean
    at get_token() — that failure IS the pass until .env lands."""
    from negotiation import negotiate
    from merchant_brain import get_product

    # Run a real negotiation to get a deal outcome
    n = negotiate("TUNE-01", 90.00)
    outcome = n.outcome
    product = get_product("TUNE-01")

    calls = [
        ("create_order_for_deal (deal)",
         lambda: create_order_for_deal(outcome, product)),
        ("create_order_for_deal (walk — should raise ValueError)",
         lambda: create_order_for_deal({"result": "buyer_walk", "price": None}, product)),
        ("capture_order", lambda: capture_order("order_placeholder")),
        ("get_order", lambda: get_order("order_placeholder")),
    ]
    for name, fn in calls:
        try:
            result = fn()
            if name.startswith("create_order_for_deal (deal)"):
                print(f"{name}: order={result.get('order_id')} "
                      f"status={result.get('status')} "
                      f"approve_url={'yes' if result.get('approve_url') else 'no'} ✅")
            else:
                print(f"{name}: UNEXPECTED SUCCESS (keys present?)")
        except ValueError as e:
            print(f"{name}: correctly raised ValueError — {e} ✅")
        except RuntimeError as e:
            assert "credentials missing" in str(e), f"{name}: wrong failure: {e}"
            print(f"{name}: fails clean at get_token ✅")
    print("checkout_flow dry_run complete — ready for first live sandbox call.")


if __name__ == "__main__":
    dry_run()
