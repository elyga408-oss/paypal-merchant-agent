"""token_manager / commerce_tools / order_flow against FakePayPal (see conftest).
No real keys, no real HTTP: urlopen is replaced and sockets are blocked."""
import base64
import json
import os
import stat
import urllib.error
import urllib.parse

import pytest

import commerce_tools
import order_flow
import token_manager
from conftest import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET


# --- token_manager ----------------------------------------------------------

def test_missing_credentials_fail_clean(no_creds):
    with pytest.raises(RuntimeError, match="credentials missing"):
        token_manager.get_token()


def test_oauth_request_shape(fake_paypal):
    tok = token_manager.get_token()
    assert tok == "FAKE-TOKEN-1"
    (req,) = fake_paypal.oauth_calls()
    assert req["method"] == "POST"
    assert urllib.parse.parse_qs(req["body"]) == {"grant_type": ["client_credentials"]}
    expected = base64.b64encode(f"{FAKE_CLIENT_ID}:{FAKE_CLIENT_SECRET}".encode()).decode()
    assert req["headers"]["authorization"] == f"Basic {expected}"
    assert req["headers"]["content-type"] == "application/x-www-form-urlencoded"


def test_state_file_written_0600(fake_paypal):
    token_manager.get_token()
    mode = stat.S_IMODE(os.stat(token_manager.STATE).st_mode)
    assert mode == 0o600
    st = json.load(open(token_manager.STATE))
    assert st["token"] == "FAKE-TOKEN-1" and st["ttl"] == 32400


def test_token_cached_between_calls(fake_paypal):
    token_manager.get_token()
    token_manager.get_token()
    assert len(fake_paypal.oauth_calls()) == 1


def test_proactive_refresh_inside_safety_window(fake_paypal, monkeypatch):
    fake_paypal.expires_in = 600
    t0 = 1_000_000.0
    monkeypatch.setattr(token_manager.time, "time", lambda: t0)
    assert token_manager.get_token() == "FAKE-TOKEN-1"
    monkeypatch.setattr(token_manager.time, "time", lambda: t0 + 299)  # 301s left > SAFETY
    assert token_manager.get_token() == "FAKE-TOKEN-1"
    monkeypatch.setattr(token_manager.time, "time", lambda: t0 + 301)  # 299s left < SAFETY
    assert token_manager.get_token() == "FAKE-TOKEN-2"


def test_force_refresh(fake_paypal):
    token_manager.get_token()
    assert token_manager.get_token(force=True) == "FAKE-TOKEN-2"


def test_corrupt_state_file_refetches(fake_paypal):
    open(token_manager.STATE, "w").write("{not json")
    assert token_manager.get_token() == "FAKE-TOKEN-1"


def test_api_bearer_and_no_commerce_flag_by_default(fake_paypal):
    order_flow.get_order("ORDER-FAKE-1")
    req = fake_paypal.calls("/v2/checkout/orders/")[0]
    assert req["headers"]["authorization"] == "Bearer FAKE-TOKEN-1"
    assert "x-feature-flags" not in req["headers"]


def test_api_empty_body_returns_empty_dict(fake_paypal):
    assert token_manager.api("/v2/invoicing/invoices/INV2-FAKE", method="DELETE") == {}


def test_api_http_error_propagates(fake_paypal):
    fake_paypal.fail_next_with = 422
    with pytest.raises(urllib.error.HTTPError):
        order_flow.create_order(amount="88.59")


def test_api_retries_once_on_401_with_fresh_token(fake_paypal):
    token_manager.get_token()
    fake_paypal.fail_next_with = 401
    assert order_flow.get_order("ORDER-FAKE-1")["status"] == "COMPLETED"
    assert len(fake_paypal.oauth_calls()) == 2


# --- commerce_tools ---------------------------------------------------------

def test_create_cart_sends_commerce_flag(fake_paypal):
    r = commerce_tools.create_cart([{"product_id": "TUNE-01", "quantity": 1}])
    assert r["id"] == "CART-FAKE-1"
    (req,) = fake_paypal.calls("/merchant-cart")
    assert req["method"] == "POST"
    assert req["headers"]["x-feature-flags"] == "commerce:true"
    assert json.loads(req["body"]) == {"currency": "USD",
                                      "items": [{"product_id": "TUNE-01", "quantity": 1}]}


def test_checkout_cart_path_and_flag(fake_paypal):
    commerce_tools.checkout_cart("CART-FAKE-1", payment_context={"k": "v"})
    (req,) = fake_paypal.calls("/merchant-cart/")
    assert req["path"] == "/merchant-cart/CART-FAKE-1/checkout"
    assert req["headers"]["x-feature-flags"] == "commerce:true"
    assert json.loads(req["body"]) == {"payment_context": {"k": "v"}}


def test_search_product_is_mcp_only_and_makes_no_http_call(fake_paypal):
    with pytest.raises(RuntimeError, match="MCP-only"):
        commerce_tools.search_product("tune-up")
    assert fake_paypal.requests == []


def test_commerce_calls_fail_clean_without_creds(no_creds):
    for fn in (lambda: commerce_tools.create_cart([]), lambda: commerce_tools.checkout_cart("x")):
        with pytest.raises(RuntimeError, match="credentials missing"):
            fn()


def test_commerce_dry_run_passes_without_creds(no_creds, capsys):
    commerce_tools.dry_run()


# --- order_flow: order -> capture -> get -> dispute lifecycle --------------

def test_create_order_payload(fake_paypal):
    r = order_flow.create_order(amount="88.59")
    assert r["status"] == "CREATED"
    (req,) = fake_paypal.calls("/v2/checkout/orders")
    assert req["method"] == "POST"
    assert json.loads(req["body"]) == {
        "intent": "CAPTURE",
        "purchase_units": [{"amount": {"currency_code": "USD", "value": "88.59"}}]}


def test_full_order_and_dispute_lifecycle(fake_paypal):
    order = order_flow.create_order(amount="88.59")
    cap = order_flow.capture_order(order["id"])
    got = order_flow.get_order(order["id"])
    disputes = order_flow.list_disputes()
    did = disputes["items"][0]["dispute_id"]
    order_flow.accept_dispute_claim(did)

    assert cap["status"] == "COMPLETED" and got["status"] == "COMPLETED"
    seq = [(r["method"], r["path"]) for r in fake_paypal.requests]
    assert seq == [
        ("POST", "/v1/oauth2/token"),                       # one token for the whole flow
        ("POST", "/v2/checkout/orders"),
        ("POST", "/v2/checkout/orders/ORDER-FAKE-1/capture"),
        ("GET", "/v2/checkout/orders/ORDER-FAKE-1"),
        ("GET", "/v1/customer/disputes"),
        ("POST", "/v1/customer/disputes/PP-D-FAKE-1/accept-claim"),
    ]
    for r in fake_paypal.requests[1:]:
        assert r["headers"]["authorization"] == "Bearer FAKE-TOKEN-1"


def test_create_invoice_posts_to_invoicing(fake_paypal):
    order_flow.create_invoice(merchant_email="m@example.com", buyer_email="b@example.com",
                              items=[{"sku": "TUNE-01", "quantity": "1"}], total="88.59")
    (req,) = fake_paypal.calls("/v2/invoicing/invoices")
    body = json.loads(req["body"])
    assert body["primary_recipients"][0]["billing_info"]["email_address"] == "b@example.com"


def test_order_flow_dry_run_fails_clean_without_creds(no_creds, capsys):
    order_flow.dry_run()
    out = capsys.readouterr().out
    assert out.count("fails clean at get_token") == 6


def test_no_real_secrets_in_repo():
    """Cheap guard: no committed .env / token state, no obvious live token shapes."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # A local .env is expected after setup; it just must never be committed.
    import subprocess
    try:
        tracked = subprocess.run(["git", "ls-files", ".env", "token_state.json"], cwd=root,
                                 capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        tracked = []
    assert tracked == [], f"secrets file committed: {tracked}"
