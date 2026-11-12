# Vega Cycle Works — Merchant Agent

**PayPal AI Hackathon 2026** — Agentic Commerce track
**License:** MIT (see [LICENSE](LICENSE))

---

## Overview

Two AI agents negotiate a bike-shop service, then the deal is paid through PayPal Orders v2 (sandbox):

- **Marisol Vega** — the merchant agent for Vega Cycle Works. Warm, no-nonsense small-biz owner who haggles but never sells below her floor price.
- **Dana** — the buyer agent, a personal shopper for a bike commuter with a budget. Opens at 62% of ask, counters geometrically, walks when the floor holds.

They negotiate agent-to-agent (A2A) → agree on a price → PayPal Orders v2 **create → buyer approves → capture**. Sandbox only, no real money.

## What's proven (and what isn't)

| Piece | Status |
|-------|--------|
| A2A negotiation (4 preset scenarios) | ✅ 4/4 verified on every run (`demo_harness.py --all`) |
| PayPal Orders v2 create → approve → capture | ✅ **Live in the PayPal sandbox**, end to end: order `708631319C9612148` created at $88.59, approved by a sandbox buyer, captured `COMPLETED` (capture `8NM99374KS917183Y`) |
| AI layer (intent + persona voice) | ✅ Live via **NVIDIA NIM**, model `nvidia/nemotron-3-ultra-550b-a55b`; deterministic fallback when no key |
| Invoices, Disputes (list → accept-claim) | 🧪 Implemented in `order_flow.py`; tested **only against a mocked PayPal** (`tests`), not live |
| Remote commerce cart tools (`create_cart` / `checkout_cart`) | ⏳ Wrapped in `commerce_tools.py`; the REST Cart API returns 403 until the account is enrolled in PayPal's agentic-commerce early access |

## Quick start

```bash
# 1. Credentials (never commit this file; it is in .gitignore)
cp .env.example .env && chmod 600 .env
#    fill PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET (sandbox app)
#    optional: NVIDIA_API_KEY for the AI layer (build.nvidia.com)

# 2. Token smoke test
python3 token_manager.py

# 3. Negotiation demo (no keys needed)
python3 demo_harness.py --all
python3 demo_harness.py --scenario A --replay 900

# 4. AI layer: free-text request + persona voice (uses NVIDIA NIM if a key is set)
python3 demo_harness.py --ask "My road bike needs a full tune-up before Sunday's ride, around \$90?" --voice

# 5. Tests (no network; PayPal and the LLM are mocked)
python3 -m pytest tests -q
```

Live sandbox checkout for a deal:

```python
from negotiation import negotiate
from merchant_brain import get_product
import checkout_flow

n = negotiate("TUNE-01", 90.00)                      # deal at $88.59
order = checkout_flow.create_order_for_deal(n.outcome, get_product("TUNE-01"))
print(order["approve_url"])                          # buyer approves in sandbox checkout
# ...after approval:
print(checkout_flow.capture_order(order["order_id"]))  # status COMPLETED
```

## Use it for another shop

Everything specific to Vega Cycle Works is in one config file, `shops/vega_cycle_works.json`. That covers the merchant and buyer personas, the price list (list price, maximum discount, bundle sweetener, stock), the haggling settings and the merchant's counter line. `shop_config.py` checks the file when it loads, so a bad price list (like a duplicate SKU, a discount of 100% or more, or a counter line with no price) fails right away instead of in the middle of a negotiation.

To show that the same agents can serve another shop, the repo includes a second, made-up shop, `shops/example_bakery.json`. Run it with `SHOP_CONFIG=shops/example_bakery.json`. The demo and the video are about Vega Cycle Works.

## Demo scenarios

| Flag | Scenario | Result |
|------|----------|--------|
| `--scenario A` | the-deal | $95 tune-up, $90 budget → **deal at $88.59** in 4 envelopes |
| `--scenario B` | the-walk | $60 budget, under the $80.75 floor → Marisol holds; **Dana walks** after 12 envelopes |
| `--scenario C` | the-fast-deal | $74 helmet, $500 budget → **deal at $70.67** in 4 envelopes (one counter) |
| `--scenario D` | the-guardrail | $80.74 budget, 1¢ under the floor → **stalemate** at the 12-envelope hard cap |

AI flags (`--ask`, `--voice`) work with any scenario. The demo prints which backend ran, e.g.
`[AI] source: llm (model nvidia/nemotron-3-ultra-550b-a55b via NVIDIA NIM)` or `[AI] source: fallback (deterministic, no LLM key)`.

## Architecture

```
┌─────────────────────┐     A2A      ┌─────────────────────┐
│   BUYER AGENT       │ ◄──────────► │  MERCHANT AGENT     │
│   (Dana, budget)    │   envelopes  │  (Marisol, floor)   │
└─────────┬───────────┘              └─────────┬───────────┘
          │      deal at an agreed price       │
          └──────────────────┬─────────────────┘
                             ▼
              PayPal Orders v2 (sandbox)
         create → buyer approves → capture
          (token_manager.py: OAuth + refresh)

   llm_layer.py (NVIDIA NIM) — reads the shopper's request and
   voices each line; never decides an action or a price
```

**Modules:**
- `token_manager.py` — OAuth client credentials, proactive refresh + one retry on 401, unique `PayPal-Request-Id` per order
- `merchant_brain.py` / `buyer_brain.py` — deterministic policy engines (prices, floors, walk-away)
- `negotiation.py` — A2A protocol: envelopes, turn-taking, terminal actions, 12-envelope cap, accept-must-match check
- `llm_layer.py` — intent parsing + persona voice over NVIDIA NIM (OpenAI-compatible), with guardrails
- `checkout_flow.py` — negotiation outcome → Orders v2 create / approve link / capture
- `order_flow.py` — Orders v2, Invoices, Disputes helpers
- `commerce_tools.py` — remote commerce cart tools wrapper (early access, 403 today)
- `demo_harness.py` — judge-facing render: transcript, price ladder, outcome box, verification, JSON export

## AI use

`llm_layer.py` adds language on top of the deterministic core:

1. **Intent parsing** — `parse_intent("…full tune-up before Sunday, around $90")` → `{sku, budget, urgency}`. The model is asked for JSON; the code re-validates (SKU must be in the catalog, budget > 0).
2. **Persona voice** — `voice(envelope, product)` writes each line as Marisol or Dana.
3. **Guardrails** — the LLM never changes an action or an amount. A voiced line is rejected (the plain line is kept) if it doesn't state the exact decided price, mentions any other dollar amount, invents perks or discounts, or claims to accept when the move was a counter. Timeouts and errors also fall back.
4. **Zero-key fallback** — without `NVIDIA_API_KEY` (or `ANTHROPIC_API_KEY`) everything runs deterministically, so the demo and tests run keyless.

Config: `NVIDIA_API_KEY` or `NVIDIA_NIM_API_KEY`; optional `NVIDIA_NIM_MODEL` (default `nvidia/nemotron-3-ultra-550b-a55b`), `NVIDIA_NIM_BASE_URL` (default `https://integrate.api.nvidia.com/v1`).

## Tools used

- **PayPal Orders v2 (sandbox REST API)** — create order, buyer approval link, capture; OAuth 2.0 client credentials
- **NVIDIA NIM** — `nvidia/nemotron-3-ultra-550b-a55b` via the OpenAI-compatible chat-completions endpoint, for intent parsing and persona voice
- **Python 3** (standard library only at runtime: `urllib`, `json`) + **pytest** for the test suite
- asciinema for the terminal recordings in the demo video

## License

MIT License — see [LICENSE](LICENSE).

**Vega Cycle Works** — agents that move money.
