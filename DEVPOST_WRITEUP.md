# Vega Cycle Works — Merchant Agent

## PayPal AI Hackathon 2026 · Agentic Commerce

**Sandbox:** PayPal sandbox only — no real money

## Inspiration

Shoppers are starting to send AI agents to buy for them, and those agents haggle. A small shop owner can't sit at the counter all night negotiating with every one of them, and can't afford to sell below cost when a bot pushes. We wanted to give the small shop an agent of its own.

## What it does

**Vega Cycle Works** is a small bike shop run by Marisol Vega — warm, no-nonsense, hand-tunes every bike the same week. Her **merchant agent** negotiates with **Dana**, a buyer agent shopping for a bike commuter with a budget.

1. A shopper describes what they need in plain words. The AI layer turns that into a product and a budget.
2. Dana and Marisol trade offers agent-to-agent (A2A envelopes: counter / accept / walk), each line voiced in persona by the LLM.
3. A deal goes straight to **PayPal Orders v2**: the order is created at the agreed price, the buyer approves in PayPal checkout, and the merchant agent captures the payment.

**Proven live in the PayPal sandbox:** order `708631319C9612148` was created at $88.59, approved by a sandbox buyer, and captured with status `COMPLETED` (capture `8NM99374KS917183Y`).

## How we built it

| Module | What it does | Status |
|--------|--------------|--------|
| token_manager.py | OAuth 2.0 client credentials, proactive refresh + one retry on 401, unique PayPal-Request-Id per order | ✅ Live in sandbox |
| merchant_brain.py | Marisol — persona, catalog, price floor, counter policy | ✅ Unit-tested |
| buyer_brain.py | Dana — budget, opening ratio, geometric counters, walk-away | ✅ Unit-tested |
| negotiation.py | A2A protocol — envelopes, turn-taking, terminal actions, 12-envelope hard cap | ✅ 4/4 scenarios verified |
| llm_layer.py | Intent parsing + persona voice via NVIDIA NIM, with guardrails | ✅ Live (Nemotron 3 Ultra) |
| checkout_flow.py | Deal → Orders v2 create → approve link → capture | ✅ Live in sandbox |
| order_flow.py | Orders v2 helpers, invoices, disputes (list → accept-claim) | 🧪 Disputes/invoices tested against a mocked PayPal only |
| commerce_tools.py | Remote commerce cart tools (`create_cart` / `checkout_cart`) | ⏳ 403 until early-access enrollment |
| demo_harness.py | Transcript, price ladder, outcome box, verification, JSON export | ✅ Verified |

## AI use

The deterministic brains are the policy engine; the LLM does the language.

1. **Intent parsing** — free text like "My road bike needs a full tune-up before Sunday's group ride. Can you get it done for around $90?" becomes `{sku: TUNE-01, budget: 90.00, urgency}`. The model returns JSON and the code re-validates it.
2. **Persona voice** — every negotiation line is written in Marisol's or Dana's voice by **NVIDIA Nemotron 3 Ultra** (`nvidia/nemotron-3-ultra-550b-a55b`) via NVIDIA NIM.
3. **Guardrails** — the LLM can never change an action or a price. A line is thrown out (the plain line is kept) if it doesn't carry the exact decided price, mentions any other amount, invents a perk or discount, or claims an accept on a counter. The demo prints the backend and the counts on screen.
4. **Keyless fallback** — with no key, everything runs deterministically, so judges can run it anywhere.

## Demo scenarios

- **A — the deal:** $95 tune-up, $90 budget. Dana opens at $58.90, Marisol counters at $88.59, Dana accepts. 4 envelopes.
- **B — the walk:** $60 budget, under the $80.75 floor. Marisol holds the line and Dana walks after 12 envelopes. "No deal" is the right answer: the shop never sells below cost.
- **C — the fast deal:** $74 helmet, $500 budget. One counter and it closes at **$70.67** in 4 envelopes.
- **D — the guardrail:** budget one cent under the floor ($80.74). Both sides keep moving, but neither crosses its line, so the 12-envelope protocol cap ends it as a stalemate.

## One shop today, a reusable pattern

Everything specific to Vega Cycle Works is in one config file, `shops/vega_cycle_works.json`. That covers the merchant and buyer personas, the price list (list price, maximum discount, bundle sweetener, stock), the haggling settings and the merchant's counter line. `shop_config.py` checks the file when it loads, so a bad price list (like a duplicate SKU, a discount of 100% or more, or a counter line with no price) fails right away instead of in the middle of a negotiation.

To show that the same agents can serve another shop, the repo includes a second, made-up shop, `shops/example_bakery.json`. Run it with `SHOP_CONFIG=shops/example_bakery.json`. The demo and the video are about Vega Cycle Works.

## Challenges

- Making the AI useful without letting it touch money: prices live only in deterministic code, and every LLM line passes a guardrail before anyone sees it.
- A real bug found during the live run: every order reused the same idempotency key, so PayPal returned an earlier order. Each order now gets a unique `PayPal-Request-Id`.
- PayPal's remote cart tools are early access; we wrapped them and used Orders v2 for the live checkout.

## What's next

- PayPal cart tools once early access opens
- Live sandbox runs of the dispute and invoice flows
- Multi-item carts, and more shops built from the same config pattern

## Tools used

- **PayPal Orders v2 (sandbox REST API)** — create, buyer approval, capture; OAuth 2.0 client credentials
- **NVIDIA NIM** — `nvidia/nemotron-3-ultra-550b-a55b` (OpenAI-compatible chat completions) for intent parsing and persona voice
- **Python 3** (standard library at runtime) + **pytest**
- asciinema for the terminal recordings in the video

## How to run

```bash
cp .env.example .env && chmod 600 .env   # add sandbox PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET, optional NVIDIA_API_KEY
python3 token_manager.py                 # token smoke test
python3 demo_harness.py --all            # 4/4 scenarios
python3 demo_harness.py --ask "My road bike needs a full tune-up before Sunday's ride, around \$90?" --voice
python3 -m pytest tests -q  # mocked PayPal + mocked LLM, no network
```

**Vega Cycle Works** — agents that move money.
