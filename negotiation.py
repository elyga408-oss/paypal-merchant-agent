#!/usr/bin/env python3
"""PayPal Merchant's Agent — MODULE 5: A2A negotiation protocol.
Built 2026-10-22. No keys required.

The protocol layer between merchant_brain.py and buyer_brain.py. Rules:

  1. ISOLATION — each side only ever sees the other's ENVELOPES:
       {"from": "merchant"|"buyer", "action": "counter"|"accept"|"decline"|"walk",
        "amount": float|None, "note": str, "turn": int}
     The Negotiation object is the wire. Brains never read each other's internals
     (budget, floor, patience) — that's what makes it A2A, not a function call.
  2. TURN-TAKING — strictly alternating, merchant states the ask first,
     buyer opens the haggling, then respond-respond until terminal.
  3. TERMINAL ACTIONS — first "accept", "decline" or "walk" ends the session.
     accept → deal at the accepted amount; decline/walk → no deal, price None.
  4. HARD CAP — max_turns envelopes total; past the cap it's a stalemate.
     Protects the demo even if a future brain regresses into a stall loop.

Deterministic end to end: same brains in, same transcript out (verified in
__main__). When the shop config changes, the brains change,
the protocol doesn't.
"""

from merchant_brain import MerchantBrain, PERSONA as M_PERSONA
from buyer_brain import BuyerBrain, BUYER_PERSONA

MAX_TURNS = 12  # hard protocol cap; our brains resolve in <= 8

ACTIONS = ("counter", "accept", "decline", "walk")
REQUIRED_KEYS = ("from", "action", "amount", "note", "turn")


class ProtocolError(Exception):
    """Raised when a brain emits something that isn't a valid envelope."""


def validate_env(env, who):
    """Protocol hygiene: every envelope that crosses the wire is checked."""
    if not isinstance(env, dict):
        raise ProtocolError(f"{who} sent {type(env).__name__}, expected dict envelope")
    missing = [k for k in REQUIRED_KEYS if k not in env]
    if missing:
        raise ProtocolError(f"{who} envelope missing keys: {missing}")
    if env["from"] != who:
        raise ProtocolError(f"{who} envelope claims from={env['from']!r}")
    if env["action"] not in ACTIONS:
        raise ProtocolError(f"{who} sent unknown action {env['action']!r}")
    if env["action"] == "counter" and not isinstance(env["amount"], (int, float)):
        raise ProtocolError(f"{who} counter without numeric amount: {env['amount']!r}")
    if env["action"] in ("accept",) and not isinstance(env["amount"], (int, float)):
        raise ProtocolError(f"{who} accept without numeric amount: {env['amount']!r}")
    return env


class Negotiation:
    """Runs one buyer-vs-merchant session. The object IS the wire."""

    def __init__(self, merchant, buyer, max_turns=MAX_TURNS):
        self.merchant = merchant
        self.buyer = buyer
        self.max_turns = max_turns
        self.transcript = []          # list of validated envelopes, in order
        self.outcome = None           # dict, set by run()

    def _send(self, env, who):
        """Validate + stamp turn + record. The only way an envelope crosses."""
        env = dict(env)                     # brains must not mutate the wire copy
        env["turn"] = len(self.transcript)
        validate_env(env, who)
        if env["action"] == "accept":
            last = next((e for e in reversed(self.transcript) if e["from"] != who), None)
            if last is None or round(float(env["amount"]), 2) != round(float(last["amount"]), 2):
                raise ProtocolError(f"{who} accepted {env['amount']!r} but the open offer is "
                                    f"{last and last['amount']!r}")
        self.transcript.append(env)
        return env

    def run(self):
        """Full session. Returns the outcome dict."""
        m_name = getattr(self.merchant, "persona", M_PERSONA).get("name", "Merchant")
        b_name = getattr(self.buyer, "persona", BUYER_PERSONA).get("name", "Buyer")

        # turn 0 — merchant states the ask
        ask_env = self._send(self.merchant.opening(), "merchant")
        ask = ask_env["amount"]

        # turn 1 — buyer opens the haggling (float -> envelope wrap)
        opening = self.buyer.opening_offer(ask)
        offer_env = self._send(
            {"from": "buyer", "action": "counter", "amount": opening,
             "note": f"Opening offer at ${opening:.2f}."},
            "buyer")

        # then strictly alternating responses, buyer's envelope in -> merchant,
        # merchant's envelope in -> buyer.
        while len(self.transcript) < self.max_turns:
            m_env = self._send(self.merchant.respond(offer_env["amount"],
                                                      offer_env["turn"]), "merchant")
            result = self._finish(m_env, "merchant", b_name)
            if result:
                return result

            b_env = self._send(self.buyer.respond_to_counter(m_env["amount"],
                                                             m_env["turn"]), "buyer")
            result = self._finish(b_env, "buyer", m_name)
            if result:
                return result
            offer_env = b_env   # next merchant turn reacts to this buyer envelope

        # hard cap reached with no terminal action
        self.outcome = {"result": "stalemate", "price": None,
                        "turns": len(self.transcript),
                        "note": f"Hit protocol cap of {self.max_turns} envelopes."}
        return self.outcome

    def _finish(self, env, who, other_name):
        """Terminal handling shared by both directions."""
        if env["action"] == "accept":
            self.outcome = {"result": "deal", "price": round(float(env["amount"]), 2),
                            "turns": len(self.transcript),
                            "note": f"{who} accepted at ${env['amount']:.2f}."}
        elif env["action"] == "walk":
            self.outcome = {"result": f"{who}_walk", "price": None,
                            "turns": len(self.transcript),
                            "note": f"{who} walked away ({other_name} keeps the floor)."}
        elif env["action"] == "decline":
            self.outcome = {"result": f"{who}_declined", "price": None,
                            "turns": len(self.transcript),
                            "note": f"{who} declined the deal."}
        else:
            return None   # counter — negotiation continues
        return self.outcome

    # --- rendering, for the demo harness (module 7) --------------------------
    def render(self):
        """Human-readable transcript lines (split-screen demo reads these)."""
        names = {"merchant": M_PERSONA.get("name", "Merchant"),
                 "buyer": BUYER_PERSONA.get("name", "Buyer")}
        lines = []
        for env in self.transcript:
            amt = f"${env['amount']:.2f}" if env["amount"] is not None else "—"
            lines.append(f"[turn {env['turn']:>2}] {env['from'].upper():>8} "
                         f"({names[env['from']]}): {env['action']:>7} {amt:>8} — {env['note']}")
        if self.outcome:
            o = self.outcome
            price = f" at ${o['price']:.2f}" if o["price"] is not None else ""
            lines.append(f"== OUTCOME: {o['result']}{price} after {o['turns']} envelopes ==")
        return "\n".join(lines)


def negotiate(product_sku, budget, **buyer_kwargs):
    """Convenience one-liner: build both brains from defaults, run, return outcome."""
    n = Negotiation(MerchantBrain(product_sku), BuyerBrain(budget, **buyer_kwargs))
    n.run()
    return n


if __name__ == "__main__":
    print("=" * 72)
    print("MODULE 5 SMOKE — A2A negotiation protocol (no PayPal keys involved)")
    print("=" * 72)

    # Scenario A: Dana's real demo case — should DEAL inside her $90 budget.
    a1 = Negotiation(MerchantBrain("TUNE-01"), BuyerBrain(90.00))
    a1.run()
    print("\n--- A: budget $90 vs Full Season Tune-Up (list $95) ---")
    print(a1.render())

    # Scenario B: hopeless budget — floor is $80.75, buyer caps at $60 → walk.
    b = Negotiation(MerchantBrain("TUNE-01"), BuyerBrain(60.00))
    b.run()
    print("\n--- B: budget $60 vs floor $80.75 (should end in a walk) ---")
    print(b.render())

    # Scenario C: generous buyer — accepts merchant's first counter, quick deal.
    c = Negotiation(MerchantBrain("HELM-02"), BuyerBrain(500.00))
    c.run()
    print("\n--- C: budget $500 vs helmet (list $74) — fast-deal path ---")
    print(c.render())

    # Determinism: identical inputs must produce identical transcripts.
    a2 = Negotiation(MerchantBrain("TUNE-01"), BuyerBrain(90.00))
    a2.run()
    assert a1.transcript == a2.transcript, "protocol is not deterministic!"

    # Invariants (persona-swap-proof: stay true for any shop config).
    assert a1.outcome["result"] == "deal", f"A should deal, got {a1.outcome}"
    assert a1.outcome["price"] >= 80.75 and a1.outcome["price"] <= 90.00, a1.outcome
    assert b.outcome["result"].endswith("_walk") and b.outcome["price"] is None, b.outcome
    assert c.outcome["result"] == "deal" and c.outcome["price"] <= 74.00, c.outcome
    for n in (a1, b, c):
        assert n.outcome["turns"] <= MAX_TURNS

    print("\nALL CHECKS PASSED — protocol deterministic, all three terminal paths live.")
