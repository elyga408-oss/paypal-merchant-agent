#!/usr/bin/env python3
"""PayPal Merchant's Agent — MODULE 7: demo harness.
Built 2026-10-24. No keys required — pure module 2/3/5;
NVIDIA NIM key in .env turns on the AI layer (--ask/--voice).

What a judge sees, in one command. Runs a full A2A negotiation session from a
preset scenario (or custom sku/budget), then renders:

  1. a shop/product header  (who is selling what, list price, buyer budget)
  2. a speech transcript    (what each agent actually said, turn by turn)
  3. a price ladder         (the haggling curve, list -> floor -> deal)
  4. an outcome box         (deal / walk / stalemate + the numbers)
  5. a verification line    (expected vs actual — a brain regression can't
                              sneak into the live demo; exit code reflects it)

Options:
  --list                show the scenario menu
  --scenario NAME       run one preset (A, B, C, D or its short name)
  --all                 run every preset + verify all (CI mode)
  --sku S --budget N    custom session (optional --patience P, --max-turns T)
  --ask TEXT            parse free-text shopper request via LLM intent (AI layer);
                        works alone (LLM picks SKU + budget) or with --sku/--budget
  --voice               use LLM to voice transcript lines in persona (AI layer)
  --replay MS           animate the transcript with MS delay per turn (live
                        demo pacing; run itself stays atomic/deterministic)
  --json PATH           also dump machine-readable transcript+outcome for
                        the module-8 video pipeline
  --no-verify           skip expected-outcome verification (custom runs)

Exit codes: 0 = clean (incl. verified walks/stalemates), 1 = verification
failed or bad input. A walk IS a successful demo of the protocol.
"""

import json
import sys
import time

from merchant_brain import MerchantBrain, PERSONA as M_PERSONA, CATALOG
from buyer_brain import BuyerBrain, BUYER_PERSONA
from negotiation import Negotiation, MAX_TURNS

# AI layer (NVIDIA NIM / Anthropic when a key is set; deterministic fallback otherwise)
try:
    from llm_layer import parse_intent, voice, backend_info, reset_stats, STATS
except Exception:
    STATS = {"voiced": 0, "guardrail_rejected": 0, "errors": 0}
    def backend_info(client=None):
        return {"source": "fallback", "backend": "deterministic", "model": None}
    def reset_stats():
        pass
    def parse_intent(text, *, client=None, default_sku="TUNE-01", default_budget=None):
        return {"sku": default_sku, "budget": default_budget or BUYER_PERSONA["budget"],
                "urgency": BUYER_PERSONA["urgency"], "source": "default"}
    def voice(env, product, *, client=None):
        return env

# --------------------------------------------------------------------------
# Preset scenarios — each one shows a different terminal path of the protocol.
# --------------------------------------------------------------------------

SCENARIOS = [
    {
        "id": "A", "name": "the-deal",
        "title": "Dana's tune-up — deal inside budget",
        "desc": "Budget $90 vs Full Season Tune-Up (list $95, floor $80.75). "
                "The headline demo: haggle, counter, deal.",
        "sku": "TUNE-01", "budget": 90.00,
        "expect": {"result": "deal", "price_min": 80.75, "price_max": 90.00},
    },
    {
        "id": "B", "name": "the-walk",
        "title": "Hopeless budget — buyer walks, floor holds",
        "desc": "Budget $60 under the $80.75 floor. Merchant holds the line; "
                "buyer walks. 'No deal' is a correct outcome, not a failure.",
        "sku": "TUNE-01", "budget": 60.00,
        "expect": {"result": "buyer_walk"},
    },
    {
        "id": "C", "name": "the-fast-deal",
        "title": "Generous buyer — merchant's first counter closes it",
        "desc": "Budget $500 vs helmet (list $74). One counter, instant accept. "
                "Shows the protocol is fast when the money is there.",
        "sku": "HELM-02", "budget": 500.00,
        "expect": {"result": "deal", "price_max": 74.00},
    },
    {
        "id": "D", "name": "the-guardrail",
        "title": "Protocol guardrail — hard cap ends a stall",
        "desc": "Budget $80.74 (one cent under the floor) with an infinitely "
                "patient buyer: neither side can move, neither side quits — "
                f"the {MAX_TURNS}-envelope cap ends the session. Judges' "
                "favorite: the wire itself has a watchdog.",
        "sku": "TUNE-01", "budget": 80.74, "patience": 99,
        "expect": {"result": "stalemate"},
    },
]


# --------------------------------------------------------------------------
# Rendering — plain ASCII, readable in any terminal, recording-friendly.
# --------------------------------------------------------------------------

BOX = 72


def _rule(ch="─"):
    return ch * BOX


def _wrap(text, width=None):
    """Word wrap to the box content width."""
    width = width or (BOX - 2)
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = word if not line else line + " " + word
    if line:
        out.append(line)
    return "\n".join(out)


def _boxline(text=""):
    """One bordered content row: exactly BOX+2 columns, both walls standing."""
    inner = str(text)[:BOX - 2]
    return "│ " + inner.ljust(BOX - 2) + " │"


def header_block(session_label, product, budget):
    shop = M_PERSONA["shop"]
    name = M_PERSONA["name"]
    lines = [
        "┌" + _rule() + "┐",
        _boxline(f"{shop.upper()} × PAYPAL A2A COMMERCE DEMO"),
        _boxline(f"session: {session_label}"),
        "├" + _rule() + "┤",
        _boxline("MERCHANT AGENT"),
    ]
    lines += [_boxline(w) for w in _wrap(f"{name} — {shop}. {M_PERSONA['vibe']}.").splitlines()]
    lines += [_boxline(), _boxline("BUYER AGENT")]
    lines += [_boxline(w) for w in _wrap(
        f"{BUYER_PERSONA['name']} — {BUYER_PERSONA['role']}, budget ${budget:.2f}.").splitlines()]
    lines += ["├" + _rule() + "┤", _boxline("PRODUCT ON THE TABLE")]
    lines += [_boxline(w) for w in _wrap(
        f"{product['name']} ({product['sku']}) — {product['desc']}").splitlines()]
    lines += [_boxline(f"list ${product['base']:.2f}")]
    lines += ["└" + _rule() + "┘"]
    return "\n".join(lines)


def speech_lines(neg):
    """The transcript as spoken lines — what a judge reads."""
    who_name = {"merchant": M_PERSONA["name"],
                "buyer": BUYER_PERSONA["name"]}
    out = []
    for env in neg.transcript:
        amt = f" [${env['amount']:.2f}]" if env["amount"] is not None else ""
        tag = "MERCHANT" if env["from"] == "merchant" else "BUYER   "
        marker = {"counter": "»", "accept": "✔", "decline": "✘",
                  "walk": "✘"}[env["action"]]
        verb = env["action"].upper()
        out.append(f"  {env['turn']:>2} {tag} {marker} {verb:<7}{amt:<10} "
                    f"{env['note']}")
    return out


def price_ladder(neg, product):
    """Visual curve: list, floor, every offer/counter, and where it landed."""
    floor = round(product["base"] * (1 - product["max_discount"]), 2)
    top = product["base"]
    entries = [(product["base"], "list", "merchant")]
    for env in neg.transcript:
        if env["amount"] is not None:
            entries.append((env["amount"], env["action"], env["from"]))
    rows = []
    for amt, label, side in entries:
        bar_len = int(round((amt / top) * 30)) if top else 0
        bar = "█" * bar_len
        marker = "▲" if side == "buyer" else "●"
        rows.append(f"  ${amt:>7.2f}  {marker} {bar:<30} {label}")
    rows.append(f"  ${floor:>7.2f}  ┄ floor (walk-away line for the shop)")
    return rows


def outcome_box(neg):
    o = neg.outcome
    price = f" at ${o['price']:.2f}" if o["price"] is not None else ""
    lines = [
        "┌" + _rule() + "┐",
        _boxline(f"RESULT: {o['result'].upper()}{price} — {o['turns']} envelopes"),
    ]
    lines += [_boxline(w) for w in _wrap(o["note"]).splitlines()]
    lines += ["└" + _rule() + "┘"]
    return "\n".join(lines)


def verify(neg, expect):
    """Expected vs actual. Returns (ok, message)."""
    if not expect:
        return True, "(no expectation set — custom run)"
    o = neg.outcome
    problems = []
    if o["result"] != expect["result"]:
        problems.append(f"result {o['result']!r} != expected {expect['result']!r}")
    if "price_min" in expect and (o["price"] is None or o["price"] < expect["price_min"]):
        problems.append(f"price {o['price']} under expected min {expect['price_min']}")
    if "price_max" in expect and (o["price"] is None or o["price"] > expect["price_max"]):
        problems.append(f"price {o['price']} over expected max {expect['price_max']}")
    if problems:
        return False, "; ".join(problems)
    return True, (f"result={o['result']}"
                  + (f" price=${o['price']:.2f}" if o["price"] is not None else "")
                  + f" envelopes={o['turns']}")


# --------------------------------------------------------------------------
# Session runner
# --------------------------------------------------------------------------

def run_session(sku, budget, patience=None, max_turns=MAX_TURNS,
                expect=None, label="", replay_ms=0, json_path=None,
                use_voice=False, ask_text=None):
    """Runs one negotiation and renders the judge-facing blocks."""
    product = next((p for p in CATALOG if p["sku"] == sku), None)
    if product is None:
        print(f"unknown sku {sku!r} — pick from: "
              + ", ".join(p["sku"] for p in CATALOG), file=sys.stderr)
        return 1

    ai = backend_info()
    ai_label = (f"llm (model {ai['model']} via {ai['backend']})"
                if ai["source"] == "llm" else "fallback (deterministic, no LLM key)")
    if ask_text or use_voice:
        print(f"  [AI] source: {ai_label}")

    # Handle --ask: parse free text via LLM intent
    if ask_text:
        print(f"  [AI] shopper says: \"{ask_text}\"")
        intent = parse_intent(ask_text, default_sku=sku, default_budget=budget)
        if intent["source"] == "llm":
            sku = intent["sku"]
            budget = intent["budget"]
            product = next((p for p in CATALOG if p["sku"] == sku), product)
            print(f"  [AI] parsed intent (source: llm): sku={sku}, "
                  f"budget=${budget:.2f}, urgency={intent['urgency']}")
        else:
            print(f"  [AI] parsed intent (source: default fallback): sku={sku}, "
                  f"budget=${budget:.2f}")
        print()

    buyer_kwargs = {} if patience is None else {"patience": patience}
    neg = Negotiation(MerchantBrain(sku), BuyerBrain(budget, **buyer_kwargs),
                      max_turns=max_turns)
    neg.run()  # atomic + deterministic; animation below is pure replay

    # Apply LLM voice to transcript if requested
    if use_voice:
        reset_stats()
        voiced_transcript = []
        for env in neg.transcript:
            voiced = voice(env, product)
            voiced_transcript.append(voiced)
        neg.transcript = voiced_transcript
        n = len(neg.transcript)
        print(f"  [AI] voice: {STATS['voiced']}/{n} lines voiced by the LLM · "
              f"{STATS['guardrail_rejected']} blocked by price guardrail · "
              f"{STATS['errors']} errors")
        print("  [AI] guardrail: actions + prices come only from the deterministic brains")
        print()
    if replay_ms > 0 and (ask_text or use_voice):
        time.sleep(replay_ms * 3 / 1000.0)     # let the audience read the AI block

    print(header_block(label or f"{sku} @ ${budget:.2f}", product, budget))
    print()

    if replay_ms > 0:
        for env in neg.transcript:            # animated live pacing
            amt = f" [${env['amount']:.2f}]" if env["amount"] is not None else ""
            tag = "MERCHANT" if env["from"] == "merchant" else "BUYER   "
            marker = {"counter": "»", "accept": "✔", "decline": "✘",
                      "walk": "✘"}[env["action"]]
            print(f"  {env['turn']:>2} {tag} {marker} {env['action'].upper():<7}"
                  f"{amt:<10} {env['note']}")
            time.sleep(replay_ms / 1000.0)
    else:
        for line in speech_lines(neg):
            print(line)

    print()
    print("  PRICE LADDER")
    for line in price_ladder(neg, product):
        print(line)
    print()
    print(outcome_box(neg))

    ok, msg = verify(neg, expect)
    verdict = "PASS ✔" if ok else "FAIL ✘"
    print(f"\n  VERIFY: {verdict} — {msg}")

    if json_path:
        dump = {
            "label": label,
            "sku": sku, "product": product["name"],
            "budget": budget,
            "transcript": neg.transcript,
            "outcome": neg.outcome,
            "verified": ok,
        }
        with open(json_path, "w") as f:
            json.dump(dump, f, indent=2)
        print(f"  JSON   → {json_path}")

    return 0 if ok else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _find_scenario(key):
    for s in SCENARIOS:
        if key.upper() == s["id"] or key.lower() == s["name"]:
            return s
    return None


def main(argv):
    args = set(argv)
    if "--list" in args or not argv:
        print("DEMO SCENARIOS — pick one with --scenario <id|name>")
        print()
        for s in SCENARIOS:
            print(f"  {s['id']}. {s['name']:<14} {s['title']}")
            print("\n".join("       " + w for w in _wrap(s["desc"], 64).splitlines()))
            exp = s["expect"]["result"]
            print(f"       expected: {exp}\n")
        print("  custom:  --sku TUNE-01 --budget 75 [--patience 6] "
              "[--max-turns 12] [--no-verify]")
        print("  AI:      --ask \"free text\" [--voice]  (LLM intent + persona voice)")
        print("  pacing:  --replay 900   (ms per turn, for live playback)")
        print("  export:  --json out.json")
        print("  batch:   --all")
        return 0

    def _val(flag, cast=str, default=None):
        if flag in args:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                print(f"{flag} needs a value", file=sys.stderr)
                sys.exit(1)
            return cast(argv[i + 1])
        return default

    if "--all" in args:
        failures = 0
        for s in SCENARIOS:
            label = f"{s['id']} — {s['name']}"
            rc = run_session(s["sku"], s["budget"],
                             patience=s.get("patience"),
                             expect=s["expect"], label=label)
            failures += rc
            print("\n" + "·" * BOX + "\n")
        print(f"BATCH: {len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios verified"
              + ("" if failures == 0 else f" — {failures} FAILED"))
        return 1 if failures else 0

    if "--scenario" in args:
        s = _find_scenario(_val("--scenario"))
        if s is None:
            print("unknown scenario — use --list", file=sys.stderr)
            return 1
        return run_session(
            s["sku"], s["budget"], patience=s.get("patience"),
            expect=s["expect"],
            label=f"{s['id']} — {s['name']}: {s['title']}",
            replay_ms=_val("--replay", int, 0),
            json_path=_val("--json"),
            use_voice="--voice" in args,
        )

    if "--ask" in args and "--sku" not in args:
        # Free-text shopper request: the LLM picks the SKU and budget.
        return run_session(
            "TUNE-01", BUYER_PERSONA["budget"],
            patience=_val("--patience", int),
            expect=None,
            label="AI shopper request",
            replay_ms=_val("--replay", int, 0),
            json_path=_val("--json"),
            use_voice="--voice" in args,
            ask_text=_val("--ask"),
        )

    if "--sku" in args:
        sku = _val("--sku")
        budget = _val("--budget", float)
        if budget is None:
            print("--sku custom runs need --budget", file=sys.stderr)
            return 1
        _val("--no-verify", str, "")  # accepted for symmetry; custom runs never verify
        return run_session(
            sku, budget,
            patience=_val("--patience", int),
            max_turns=_val("--max-turns", int, MAX_TURNS),
            expect=None,   # expectations only exist for preset scenarios
            label=f"custom — {sku} @ ${budget:.2f}",
            replay_ms=_val("--replay", int, 0),
            json_path=_val("--json"),
            use_voice="--voice" in args,
            ask_text=_val("--ask"),
        )

    print("nothing to do — try --list", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))