import pytest

from merchant_brain import CATALOG, PATIENCE, MerchantBrain, get_product


@pytest.mark.parametrize("sku,floor", [("TUNE-01", 80.75), ("HELM-02", 66.60), ("LAMP-03", 38.40)])
def test_floor_matches_catalog_comments(sku, floor):
    assert MerchantBrain(sku).floor == floor


def test_unknown_sku_raises():
    with pytest.raises(KeyError):
        get_product("NOPE-99")


def test_opening_is_list_price():
    env = MerchantBrain("TUNE-01").opening()
    assert env["from"] == "merchant" and env["action"] == "counter" and env["amount"] == 95.00


def test_first_counter_value():
    # base - (base - floor) * 0.45 = 95 - 14.25*0.45 = 88.5875 -> 88.59
    env = MerchantBrain("TUNE-01").respond(58.90, 1)
    assert env["action"] == "counter" and env["amount"] == 88.59


def test_accepts_offer_at_or_above_next_counter():
    env = MerchantBrain("TUNE-01").respond(88.59, 1)
    assert env["action"] == "accept" and env["amount"] == 88.59


@pytest.mark.parametrize("sku", [p["sku"] for p in CATALOG])
def test_never_counters_or_accepts_below_floor(sku):
    """Sweep buyer offers from 0 to list price on a fresh brain each time."""
    base = get_product(sku)["base"]
    for cents in range(0, int(base * 100) + 1, 37):
        m = MerchantBrain(sku)
        env = m.respond(cents / 100, 1)
        if env["amount"] is not None:
            assert env["amount"] >= m.floor, (sku, cents, env)


@pytest.mark.parametrize("sku", [p["sku"] for p in CATALOG])
def test_counters_are_monotonic_non_increasing(sku):
    m = MerchantBrain(sku)
    amounts = []
    for t in range(10):
        env = m.respond(1.00, t)          # lowball forever
        if env["action"] != "counter":
            break
        amounts.append(env["amount"])
    assert amounts == sorted(amounts, reverse=True)
    assert all(a >= m.floor for a in amounts)
    assert env["action"] == "walk"   # lowballer gets walked on after PATIENCE counters


def test_sweetener_offered_once_within_window():
    m = MerchantBrain("TUNE-01")           # floor 80.75, window 5% = 4.04
    first = m.respond(78.00, 1)
    assert first["action"] == "counter" and first["amount"] == 80.75
    assert "bottle cage" in first["note"]
    second = m.respond(78.00, 3)
    assert "bottle cage" not in second["note"]


def test_merchant_walks_on_lowball_after_patience():
    m = MerchantBrain("TUNE-01")
    actions = [m.respond(10.00, t)["action"] for t in range(PATIENCE + 1)]
    assert actions[:PATIENCE] == ["counter"] * PATIENCE
    assert actions[PATIENCE] == "walk"
