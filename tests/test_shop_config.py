import copy
import json

import pytest

import shop_config
from shop_config import ShopConfigError, load, validate


def base():
    return copy.deepcopy(load())


def test_default_is_vega():
    cfg = load()
    assert cfg["merchant"]["shop"] == "Vega Cycle Works"
    assert [p["sku"] for p in cfg["catalog"]] == ["TUNE-01", "HELM-02", "LAMP-03"]


def test_brains_read_config():
    import merchant_brain, buyer_brain
    assert merchant_brain.PERSONA["name"] == shop_config.SHOP["merchant"]["name"]
    assert merchant_brain.CATALOG[0]["base"] == 95.00
    assert merchant_brain.PATIENCE == 6
    assert buyer_brain.BUYER_PERSONA["budget"] == 90.00


def test_other_shop_file_loads(tmp_path, monkeypatch):
    cfg = base()
    cfg["merchant"]["shop"] = "Example Bakery"
    cfg["catalog"] = [{"sku": "LOAF-1", "name": "Sourdough", "desc": "1 kg", "base": 9.5,
                       "max_discount": 0.1, "bundle_sweetener": "free butter", "stock": 20}]
    f = tmp_path / "bakery.json"
    f.write_text(json.dumps(cfg))
    monkeypatch.setenv("SHOP_CONFIG", str(f))
    assert load()["merchant"]["shop"] == "Example Bakery"


@pytest.mark.parametrize("mutate, msg", [
    (lambda c: c["catalog"].append(dict(c["catalog"][0])), "duplicate sku"),
    (lambda c: c["catalog"][0].update(base=0), "base must be > 0"),
    (lambda c: c["catalog"][0].update(max_discount=1.2), "max_discount"),
    (lambda c: c["catalog"][0].update(base="95"), "expected a number"),
    (lambda c: c["catalog"][0].update(stock=-1), "stock"),
    (lambda c: c.update(catalog=[]), "catalog is empty"),
    (lambda c: c["merchant"].pop("shop"), "missing 'shop'"),
    (lambda c: c["buyer"].update(budget=-5), "budget"),
    (lambda c: c["policy"].update(patience=0), "patience"),
    (lambda c: c["policy"].update(concession_decay=1.5), "concession_decay"),
])
def test_rejects_bad_config(mutate, msg):
    cfg = base()
    mutate(cfg)
    with pytest.raises(ShopConfigError, match=msg):
        validate(cfg)


def test_missing_and_bad_json(tmp_path):
    with pytest.raises(ShopConfigError, match="not found"):
        load(tmp_path / "nope.json")
    f = tmp_path / "bad.json"
    f.write_text("{not json")
    with pytest.raises(ShopConfigError, match="invalid JSON"):
        load(f)


def test_keywords_validated():
    cfg = base()
    cfg["catalog"][0]["keywords"] = "tune"
    with pytest.raises(ShopConfigError, match="keywords"):
        validate(cfg)


def test_default_sku_comes_from_config():
    import llm_layer
    assert llm_layer.parse_intent("anything", client=None)["sku"] == "TUNE-01"


@pytest.mark.parametrize("line", ["no price here", "{price} and {other}", 7])
def test_counter_line_needs_price(line):
    cfg = base()
    cfg["merchant"]["counter_line"] = line
    with pytest.raises(ShopConfigError, match="counter_line"):
        validate(cfg)


def test_example_bakery_negotiates(monkeypatch):
    import subprocess, sys, os
    env = dict(os.environ, SHOP_CONFIG="shops/example_bakery.json")
    out = subprocess.run([sys.executable, "-c",
        "from negotiation import Negotiation; from merchant_brain import MerchantBrain; "
        "from buyer_brain import BuyerBrain, BUYER_PERSONA; "
        "r = Negotiation(MerchantBrain('BOX-12'), BuyerBrain(BUYER_PERSONA['budget'])).run(); "
        "print(r['result'], r['price'])"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env, capture_output=True, text=True, check=True).stdout.split()
    assert out[0] == "deal" and 36.96 <= float(out[1]) <= 40.00
