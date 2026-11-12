"""Shared fixtures. Nothing here talks to the network: a socket guard fails any
real connection attempt, and FakePayPal replaces urllib.request.urlopen."""
import io
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAKE_CLIENT_ID = "fake-client-id"        # not a real credential
FAKE_CLIENT_SECRET = "fake-client-secret"  # not a real credential


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    """Tests never read the real .env or a real LLM key."""
    monkeypatch.setenv("VEGA_NO_DOTENV", "1")
    for k in ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "NVIDIA_NIM_MODEL",
              "NVIDIA_NIM_BASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real socket connect is a test failure: we never call PayPal."""
    def _blocked(*a, **k):
        raise AssertionError("real network access attempted during tests")
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakePayPal:
    """Minimal in-memory stand-in for api-m.sandbox.paypal.com.

    Records every request as dict(url, path, method, headers (lower-cased), body).
    Routes are matched on (method, path prefix). Set `fail_next_with` to an HTTP
    status to make the next non-OAuth call fail once (e.g. 401 for expiry tests).
    """

    def __init__(self):
        self.requests = []
        self.tokens_issued = 0
        self.expires_in = 32400
        self.fail_next_with = None

    # -- helpers -----------------------------------------------------------
    def calls(self, path_prefix=None):
        return [r for r in self.requests
                if path_prefix is None or r["path"].startswith(path_prefix)]

    def oauth_calls(self):
        return self.calls("/v1/oauth2/token")

    # -- urlopen replacement -----------------------------------------------
    def urlopen(self, req, timeout=None):
        url = req.full_url
        parsed = urllib.parse.urlparse(url)
        assert parsed.hostname == "api-m.sandbox.paypal.com", f"non-sandbox host: {url}"
        body = req.data.decode() if req.data else None
        rec = {
            "url": url, "path": parsed.path, "method": req.get_method(),
            "headers": {k.lower(): v for k, v in req.header_items()},
            "body": body,
        }
        self.requests.append(rec)
        payload = self._route(rec, req)
        return _Resp(b"" if payload is None else json.dumps(payload).encode())

    def _route(self, rec, req):
        m, p = rec["method"], rec["path"]
        if p == "/v1/oauth2/token":
            assert m == "POST"
            self.tokens_issued += 1
            return {"access_token": f"FAKE-TOKEN-{self.tokens_issued}",
                    "token_type": "Bearer", "expires_in": self.expires_in}
        if self.fail_next_with:
            code, self.fail_next_with = self.fail_next_with, None
            raise urllib.error.HTTPError(rec["url"], code, "fake error", {},
                                         io.BytesIO(b'{"name":"INVALID_TOKEN"}'))
        if m == "POST" and p == "/v2/checkout/orders":
            return {"id": "ORDER-FAKE-1", "status": "CREATED",
                    "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER-FAKE-1"}]}
        if m == "DELETE":
            return None  # 204 No Content
        if m == "POST" and p.startswith("/v2/checkout/orders/") and p.endswith("/capture"):
            return {"id": p.split("/")[4], "status": "COMPLETED"}
        if m == "GET" and p.startswith("/v2/checkout/orders/"):
            return {"id": p.split("/")[4], "status": "COMPLETED"}
        if m == "POST" and p == "/v2/invoicing/invoices":
            return {"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-FAKE"}
        if m == "GET" and p == "/v1/customer/disputes":
            return {"items": [{"dispute_id": "PP-D-FAKE-1", "status": "OPEN"}]}
        if m == "POST" and p.startswith("/v1/customer/disputes/") and p.endswith("/accept-claim"):
            return {"links": [{"rel": "self", "href": p}]}
        if m == "POST" and p == "/merchant-cart":
            return {"id": "CART-FAKE-1", "status": "CREATED"}
        if m == "POST" and p.startswith("/merchant-cart/") and p.endswith("/checkout"):
            return {"id": "ORDER-FROM-CART-1", "status": "APPROVED"}
        raise AssertionError(f"FakePayPal: unrouted {m} {p}")


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    """Point token_manager at a temp .env/state with FAKE credentials."""
    import token_manager
    env = tmp_path / ".env"
    env.write_text(f'PAYPAL_CLIENT_ID="{FAKE_CLIENT_ID}"\nPAYPAL_CLIENT_SECRET={FAKE_CLIENT_SECRET}\n')
    monkeypatch.setattr(token_manager, "ENV", str(env))
    monkeypatch.setattr(token_manager, "STATE", str(tmp_path / "token_state.json"))
    return tmp_path


@pytest.fixture
def no_creds(tmp_path, monkeypatch):
    import token_manager
    monkeypatch.setattr(token_manager, "ENV", str(tmp_path / "missing.env"))
    monkeypatch.setattr(token_manager, "STATE", str(tmp_path / "token_state.json"))
    return tmp_path


@pytest.fixture
def fake_paypal(monkeypatch, token_env):
    fake = FakePayPal()
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    return fake
