#!/usr/bin/env python3
"""PayPal Merchant's Agent — MODULE 3: buyer brain (sandbox demo).
Built 2026-10-22. No keys required.

Shopper-for-X: budget, wants, counter-offer logic. Deterministic:
counter-offers step toward the merchant's last ask with a fixed geometric
stride, capped by the buyer's budget and by a patience limit.
Envelope format mirrors merchant_brain.py.
"""

from shop_config import SHOP

_BUYER = SHOP["buyer"]
BUYER_PERSONA = {k: _BUYER[k] for k in ("name", "role", "budget", "urgency", "ask_style") if k in _BUYER}
BUYER_PERSONA["budget"] = float(BUYER_PERSONA["budget"])
BUYER_EMAIL = _BUYER.get("email", "buyer@example.com")

class BuyerBrain:
    """Counter-offer ladder for the buyer agent."""

    def __init__(self, budget, opening_ratio=0.62, gap_step=0.30, patience=4):
        self.budget = float(budget)
        self.opening_ratio = opening_ratio   # first offer = ask * ratio
        self.gap_step = gap_step            # each counter closes 30% toward the ask
        self.patience = patience
        self.counters = 0
        self.last_offer = None

    def opening_offer(self, merchant_ask):
        """First offer: a fixed ratio of the merchant's ask, under budget."""
        ask = float(merchant_ask)
        offer = round(ask * self.opening_ratio, 2)
        offer = min(offer, self.budget)
        self.last_offer = offer
        return offer

    def respond_to_counter(self, merchant_counter, turn):
        """React to a merchant counter. Returns an envelope."""
        ask = round(float(merchant_counter), 2)

        # Budget clear: accept the merchant's price.
        if ask <= self.budget:
            return self._env("accept", ask, "Within budget — take the deal.", turn)

        self.counters += 1
        if self.counters > self.patience:
            return self._env("walk", None,
                             f"Over my client's ${self.budget:.2f} budget — thanks, walking away.", turn)

        # Counter: step toward the ask but never cross the budget.
        target = self.last_offer + (ask - self.last_offer) * self.gap_step
        target = min(round(target, 2), self.budget)
        if target <= self.last_offer:
            # already at budget; one more polite push, then walk
            return self._env("counter", self.budget,
                             f"Client budget caps at ${self.budget:.2f} — can you do it?", turn)
        self.last_offer = target
        return self._env("counter", target,
                         f"Can you meet at ${target:.2f}?", turn)

    def _env(self, action, amount, note, turn):
        return {"from": "buyer", "action": action,
                "amount": amount, "note": note, "turn": turn}


if __name__ == "__main__":
    # smoke test — no PayPal keys involved
    b = BuyerBrain(BUYER_PERSONA["budget"])
    print("buyer-only smoke: budget", BUYER_PERSONA["budget"], "opening on $95 ->", b.opening_offer(95.00))
