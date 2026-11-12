import runpy

import pytest

from buyer_brain import BuyerBrain


def test_opening_offer_is_ratio_of_ask():
    assert BuyerBrain(90).opening_offer(95.00) == 58.90


def test_opening_offer_capped_by_budget():
    assert BuyerBrain(10).opening_offer(95.00) == 10.00


def test_accepts_when_ask_within_budget():
    b = BuyerBrain(90)
    b.opening_offer(95)
    env = b.respond_to_counter(88.59, 2)
    assert env["action"] == "accept" and env["amount"] == 88.59


def test_counter_never_exceeds_budget_and_is_monotonic():
    b = BuyerBrain(70, patience=50)
    b.opening_offer(95)
    offers = []
    for t in range(20):
        env = b.respond_to_counter(95.00, t)
        assert env["action"] == "counter"
        assert env["amount"] <= 70.00
        offers.append(env["amount"])
    assert offers == sorted(offers)
    assert offers[-1] == 70.00


def test_walks_after_patience():
    b = BuyerBrain(60, patience=4)
    b.opening_offer(95)
    actions = [b.respond_to_counter(90.00, t)["action"] for t in range(5)]
    assert actions == ["counter"] * 4 + ["walk"]


def test_module_smoke_runs():
    import buyer_brain
    runpy.run_path(buyer_brain.__file__, run_name="__main__")
