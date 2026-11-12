#!/usr/bin/env python3
"""PayPal Merchant's Agent — token manager (sandbox).
Built 2026-10-19.

Auto-refreshes OAuth tokens before expiry (sandbox tokens last 3-8h).
Reads PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET from the environment, else from
.env next to this file (or $VEGA_HOME/.env). Persists token + expiry to
token_state.json in the same folder (gitignored).
Call get_token() anywhere in the app; it refreshes proactively.
"""
import json, uuid, os, time, urllib.request, urllib.parse, urllib.error, base64

BASE = os.environ.get("VEGA_HOME") or os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(BASE, ".env")
STATE = os.path.join(BASE, "token_state.json")
OAUTH = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
SAFETY = 300  # refresh 5 min before expiry

def _read_env():
    cid = os.environ.get("PAYPAL_CLIENT_ID") or None
    csec = os.environ.get("PAYPAL_CLIENT_SECRET") or None
    if cid and csec:
        return cid, csec
    if os.path.exists(ENV):
        for line in open(ENV):
            line = line.strip()
            if line.startswith("PAYPAL_CLIENT_ID="):
                cid = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("PAYPAL_CLIENT_SECRET="):
                csec = line.split("=", 1)[1].strip().strip('"')
    return cid, csec

def _fetch_token(cid, csec):
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(OAUTH, data=data, method="POST")
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    return d["access_token"], int(d.get("expires_in", 32400))

def get_token(force=False):
    """Return a valid access token, refreshing early if needed."""
    cid, csec = _read_env()
    if not cid or not csec:
        raise RuntimeError("PayPal credentials missing: set PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET (environment or .env next to token_manager.py)")
    st = {}
    if os.path.exists(STATE):
        try:
            st = json.load(open(STATE))
        except Exception:
            st = {}
    now = time.time()
    if not force and st.get("token") and now < st.get("expires_at", 0) - SAFETY:
        return st["token"]
    token, ttl = _fetch_token(cid, csec)
    st = {"token": token, "expires_at": now + ttl, "ttl": ttl, "fetched_at": now}
    json.dump(st, open(STATE, "w"), indent=2)
    os.chmod(STATE, 0o600)
    return token

def api(path, method="GET", payload=None, commerce=False, _retried=False):
    """Authorized sandbox API call. commerce=True adds the feature-flag header
    required by search_product/create_cart/checkout_cart."""
    token = get_token()
    url = f"https://api-m.sandbox.paypal.com{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    if commerce:
        req.add_header("x-feature-flags", "commerce:true")
    # Idempotency key on state-changing calls so a retry can't double-charge
    if method in ("POST", "PUT", "PATCH"):
        req.add_header("PayPal-Request-Id", f"vega-{method.lower()}-{path.strip('/').replace('/', '-')}-{uuid.uuid4().hex[:12]}")  # unique per call: a fixed key made PayPal return an old order
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code == 401 and not _retried:
            get_token(force=True)
            return api(path, method, payload, commerce, _retried=True)
        raise

if __name__ == "__main__":
    t = get_token()
    st = json.load(open(STATE))
    mins = int((st["expires_at"] - time.time()) / 60)
    print(f"TOKEN OK · ttl {st['ttl']}s · valid for ~{mins} more minutes")
    print("token head:", t[:12], "…")
