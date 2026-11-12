#!/usr/bin/env python3
"""PayPal Merchant's Agent — MODULE 2: merchant brain (sandbox demo).
Built 2026-10-22. No keys required.

Persona + catalog + haggling policy. Deterministic (no LLM, no RNG):
same inputs -> same negotiation. Persona, catalog and knobs come from the shop config file (shop_config.py).

Envelope format (A2A-style, consumed by negotiation.py):
  {"from": "merchant", "action": "counter"|"accept"|"decline"|"walk",
   "amount": float|None, "note": str, "turn": int}
"""

from shop_config import SHOP

# Persona, price list and haggling knobs live in shops/*.json (see shop_config.py).
# Each product carries its own policy: floor = base * (1 - max_discount).
_MERCHANT = SHOP["merchant"]
PERSONA = {k: _MERCHANT[k] for k in ("name", "shop", "vibe", "greeting")}
COUNTER_LINE = _MERCHANT.get("counter_line", "I can do {price}.")
MERCHANT_EMAIL = _MERCHANT.get("email", "merchant@example.com")
CATALOG = [dict(p, stock=p.get("stock")) for p in SHOP["catalog"]]

# --- Policy knobs -----------------------------------------------------------
CONCESSION_DECAY = SHOP["policy"]["concession_decay"]   # share of gap kept per counter
SWEETENER_WINDOW = SHOP["policy"]["sweetener_window"]   # within this % under floor: sweeten once
PATIENCE = SHOP["policy"]["patience"]                   # max counters before walking

def get_product(sku):
    for p in CATALOG:
        if p["sku"] == sku:
            return p
    raise KeyError(f"sku {sku!r} not in catalog")


class MerchantBrain:
    """Price policy + haggling rules. Deterministic."""

    def __init__(self, product_sku, persona=None):
        self.product = get_product(product_sku)
        self.persona = persona or PERSONA
        self.base = self.product["base"]
        self.floor = round(self.base * (1 - self.product["max_discount"]), 2)
        self.last_counter = None
        self.counters = 0
        self.sweetener_used = False
        self.buyer_last_offer = None

    # --- policy --------------------------------------------------------------
    def _next_counter(self):
        """Geometric approach from base toward the floor."""
        if self.last_counter is None:
            nxt = self.base - (self.base - self.floor) * (1 - CONCESSION_DECAY)
        else:
            nxt = self.floor + (self.last_counter - self.floor) * CONCESSION_DECAY
        # always at least the floor, and never above the last counter
        nxt = max(self.floor, min(nxt, self.last_counter or self.base))
        return round(nxt, 2)

    def respond(self, buyer_offer, turn):
        """Return an envelope reacting to the buyer's offer."""
        self.buyer_last_offer = buyer_offer
        buyer_offer = round(float(buyer_offer), 2)

        # Accept: buyer meets (or beats) what I was about to ask next.
        if buyer_offer >= self._next_counter():
            return self._env("accept", buyer_offer,
                             f"Deal — {self.persona['name']}: done at ${buyer_offer:.2f}. Cash or card?", turn)

        # Sweetener: within window under floor, bundle once instead of dropping price.
        gap = self.floor - buyer_offer
        if 0 < gap <= max(self.floor * SWEETENER_WINDOW, 0.01) and not self.sweetener_used:
            self.sweetener_used = True
            self.last_counter = self.floor
            self.counters += 1
            return self._env(
                "counter", self.floor,
                f"I can't go below ${self.floor:.2f} on the {self.product['name']} — "
                f"but meet me there and I'll throw in a {self.product['bundle_sweetener']}.", turn)

        # Below floor outright: hold the line, say why (small-biz charm).
        if buyer_offer < self.floor * 0.92 and self.counters >= PATIENCE:
            return self._env("walk", None,
                             "That's under my cost of doing it right. I'll pass — no hard feelings.", turn)

        # Standard counter.
        nxt = self._next_counter()
        if self.last_counter is not None and nxt >= self.last_counter - 0.01:
            # stalled at the floor: restate it, firmly
            self.last_counter = self.floor
            self.counters += 1
            return self._env("counter", self.floor,
                             f"${self.floor:.2f} is my bottom line on this.", turn)
        self.last_counter = nxt
        self.counters += 1
        return self._env("counter", nxt,
                         COUNTER_LINE.format(price=f"${nxt:.2f}"), turn)

    def opening(self, turn=0):
        """List/price statement when the buyer asks about the product."""
        return self._env("counter", self.base,
                         f"{self.product['name']} — ${self.base:.2f}. {self.persona['greeting']}", turn)

    def _env(self, action, amount, note, turn):
        return {"from": "merchant", "action": action,
                "amount": amount, "note": note, "turn": turn}
