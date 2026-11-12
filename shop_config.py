#!/usr/bin/env python3
"""Load the shop (merchant persona, buyer persona, price list, haggling knobs)
from a JSON file so the same agents can run a different shop without code edits.

Default: shops/vega_cycle_works.json. Override with SHOP_CONFIG=path/to/shop.json.
Validation is strict on purpose: a bad price list fails at import, not mid-haggle.
"""
import json
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DEFAULT_PATH = _HERE / "shops" / "vega_cycle_works.json"


class ShopConfigError(ValueError):
    pass


def _need(d, key, where, typ):
    if key not in d:
        raise ShopConfigError(f"{where}: missing '{key}'")
    v = d[key]
    if typ is float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ShopConfigError(f"{where}.{key}: expected a number")
        return float(v)
    if not isinstance(v, typ):
        raise ShopConfigError(f"{where}.{key}: expected {typ.__name__}")
    return v


def validate(cfg):
    if not isinstance(cfg, dict):
        raise ShopConfigError("shop config must be a JSON object")
    m = _need(cfg, "merchant", "shop", dict)
    for k in ("name", "shop", "vibe", "greeting"):
        _need(m, k, "merchant", str)
    cl = m.get("counter_line", "I can do {price}.")
    if not isinstance(cl, str) or "{price}" not in cl or cl.count("{") != 1 or cl.count("}") != 1:
        raise ShopConfigError("merchant.counter_line must be text containing {price} exactly once")
    b = _need(cfg, "buyer", "shop", dict)
    for k in ("name", "role", "urgency"):
        _need(b, k, "buyer", str)
    if _need(b, "budget", "buyer", float) <= 0:
        raise ShopConfigError("buyer.budget must be > 0")
    p = _need(cfg, "policy", "shop", dict)
    if not 0 < _need(p, "concession_decay", "policy", float) < 1:
        raise ShopConfigError("policy.concession_decay must be between 0 and 1")
    if not 0 <= _need(p, "sweetener_window", "policy", float) < 1:
        raise ShopConfigError("policy.sweetener_window must be between 0 and 1")
    pat = _need(p, "patience", "policy", int)
    if isinstance(pat, bool) or pat < 1:
        raise ShopConfigError("policy.patience must be an integer >= 1")
    cat = _need(cfg, "catalog", "shop", list)
    if not cat:
        raise ShopConfigError("catalog is empty")
    seen = set()
    for i, item in enumerate(cat):
        w = f"catalog[{i}]"
        if not isinstance(item, dict):
            raise ShopConfigError(f"{w}: expected an object")
        sku = _need(item, "sku", w, str)
        if sku in seen:
            raise ShopConfigError(f"{w}: duplicate sku {sku!r}")
        seen.add(sku)
        for k in ("name", "desc", "bundle_sweetener"):
            _need(item, k, w, str)
        if _need(item, "base", w, float) <= 0:
            raise ShopConfigError(f"{w}.base must be > 0")
        if not 0 <= _need(item, "max_discount", w, float) < 1:
            raise ShopConfigError(f"{w}.max_discount must be between 0 and 1")
        kw = item.get("keywords", [])
        if not isinstance(kw, list) or not all(isinstance(w, str) and w for w in kw):
            raise ShopConfigError(f"{w}.keywords must be a list of words")
        stock = item.get("stock")
        if stock is not None and (isinstance(stock, bool) or not isinstance(stock, int) or stock < 0):
            raise ShopConfigError(f"{w}.stock must be null (service) or an integer >= 0")
    return cfg


def load(path=None):
    path = Path(path or os.environ.get("SHOP_CONFIG") or DEFAULT_PATH)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ShopConfigError(f"shop config not found: {path}") from None
    except json.JSONDecodeError as e:
        raise ShopConfigError(f"{path}: invalid JSON ({e})") from None
    cfg = validate(cfg)
    cfg.pop("_comment", None)
    return cfg


SHOP = load()
