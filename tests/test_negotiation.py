import json
import os

import pytest

import demo_harness
from buyer_brain import BuyerBrain
from merchant_brain import CATALOG, MerchantBrain
from negotiation import MAX_TURNS, Negotiation, ProtocolError, negotiate, validate_env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Exact transcripts observed on this snapshot (2026-11-04). If a brain knob
# changes on purpose, update these numbers — they are regression pins.
EXPECTED = {
    "A": ("deal", 88.59, 4),
    "B": ("buyer_walk", None, 12),
    "C": ("deal", 70.67, 4),
    "D": ("stalemate", None, 12),
}


def _run(s):
    kw = {} if s.get("patience") is None else {"patience": s["patience"]}
    n = Negotiation(MerchantBrain(s["sku"]), BuyerBrain(s["budget"], **kw))
    n.run()
    return n


@pytest.mark.parametrize("s", demo_harness.SCENARIOS, ids=lambda s: s["id"])
def test_scenario_outcomes(s):
    n = _run(s)
    result, price, turns = EXPECTED[s["id"]]
    assert n.outcome["result"] == result
    assert n.outcome["price"] == price
    assert n.outcome["turns"] == turns
    ok, msg = demo_harness.verify(n, s["expect"])
    assert ok, msg


@pytest.mark.parametrize("s", demo_harness.SCENARIOS, ids=lambda s: s["id"])
def test_determinism(s):
    assert _run(s).transcript == _run(s).transcript


@pytest.mark.parametrize("s", demo_harness.SCENARIOS, ids=lambda s: s["id"])
def test_wire_invariants(s):
    n = _run(s)
    t = n.transcript
    assert len(t) <= MAX_TURNS
    assert [e["turn"] for e in t] == list(range(len(t)))
    assert [e["from"] for e in t] == ["merchant", "buyer"] * (len(t) // 2) + ["merchant"] * (len(t) % 2)
    # only the last envelope may be terminal
    assert all(e["action"] == "counter" for e in t[:-1])


def test_walk_away_price_is_none_and_floor_holds():
    n = negotiate("TUNE-01", 60.00)
    assert n.outcome["result"] == "buyer_walk" and n.outcome["price"] is None
    assert min(e["amount"] for e in n.transcript if e["from"] == "merchant") >= 80.75


def test_hard_cap_ends_stall():
    n = negotiate("TUNE-01", 80.74, patience=99)
    assert n.outcome["result"] == "stalemate" and n.outcome["turns"] == MAX_TURNS


def test_custom_cap_respected():
    n = Negotiation(MerchantBrain("TUNE-01"), BuyerBrain(80.74, patience=99), max_turns=6)
    n.run()
    assert n.outcome["result"] == "stalemate" and len(n.transcript) == 6


@pytest.mark.parametrize("sku", [p["sku"] for p in CATALOG])
def test_budget_sweep_never_sells_below_floor_or_above_budget(sku):
    floor = MerchantBrain(sku).floor
    base = next(p["base"] for p in CATALOG if p["sku"] == sku)
    for cents in range(int(base * 50), int(base * 130), 7):
        b = cents / 100
        o = negotiate(sku, b).outcome
        if o["result"] == "deal":
            assert floor <= o["price"] <= b, (sku, b, o)
        if b < floor:
            assert o["result"] != "deal", (sku, b, o)


@pytest.mark.xfail(strict=True, reason="REVIEW.md R6: HELM-02 budgets $66.60-$66.96 (at/above the "
                   "$66.60 floor) end in buyer_walk — buyer patience runs out before the merchant reaches the floor")
def test_deal_whenever_budget_covers_floor_helmet():
    m = MerchantBrain("HELM-02")
    misses = [c / 100 for c in range(int(m.floor * 100), 7401)
              if negotiate("HELM-02", c / 100).outcome["result"] != "deal"]
    assert misses == []


def test_brain_cannot_mutate_recorded_envelope():
    class LeakyMerchant(MerchantBrain):
        def opening(self, turn=0):
            self.leak = super().opening(turn)
            return self.leak

    m = LeakyMerchant("TUNE-01")
    n = Negotiation(m, BuyerBrain(90))
    n.run()
    m.leak["amount"] = 1.00
    assert n.transcript[0]["amount"] == 95.00


@pytest.mark.parametrize("env,who,msg", [
    ([], "buyer", "expected dict"),
    ({"from": "buyer"}, "buyer", "missing keys"),
    ({"from": "merchant", "action": "counter", "amount": 1, "note": "", "turn": 0}, "buyer", "claims from"),
    ({"from": "buyer", "action": "bribe", "amount": 1, "note": "", "turn": 0}, "buyer", "unknown action"),
    ({"from": "buyer", "action": "counter", "amount": "1", "note": "", "turn": 0}, "buyer", "counter without"),
    ({"from": "buyer", "action": "accept", "amount": None, "note": "", "turn": 0}, "buyer", "accept without"),
])
def test_validate_env_rejects(env, who, msg):
    with pytest.raises(ProtocolError, match=msg):
        validate_env(env, who)


def test_invalid_brain_output_raises_protocol_error():
    class Broken(BuyerBrain):
        def respond_to_counter(self, c, t):
            return {"from": "buyer", "action": "haggle", "amount": 1, "note": "", "turn": t}

    with pytest.raises(ProtocolError):
        Negotiation(MerchantBrain("TUNE-01"), Broken(50)).run()


def test_accept_must_match_counterparty_amount():
    class Rogue(BuyerBrain):
        def respond_to_counter(self, c, t):
            return {"from": "buyer", "action": "accept", "amount": 1.00, "note": "", "turn": t}

    with pytest.raises(ProtocolError):
        Negotiation(MerchantBrain("TUNE-01"), Rogue(50)).run()


def test_harness_all_scenarios_exit_zero(capsys):
    assert demo_harness.main(["--all"]) == 0
    assert "BATCH: 4/4 scenarios verified" in capsys.readouterr().out


def test_harness_json_matches_checked_in_demo_session(tmp_path, capsys):
    out = tmp_path / "a.json"
    assert demo_harness.main(["--scenario", "A", "--json", str(out)]) == 0
    got = json.loads(out.read_text())
    want = json.load(open(os.path.join(ROOT, "demo_session_A.json")))
    assert got == want
