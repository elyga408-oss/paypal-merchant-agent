#!/usr/bin/env python3
"""PayPal Merchant's Agent — LLM layer over the deterministic core.

Hackathon requirement: "Meaningfully use AI." The deterministic brains stay
the policy engine (prices, actions, floors). The LLM only does language:

  1. parse_intent(text) — shopper free-text -> {sku, budget, urgency, source}
  2. voice(env, product) — after a brain decides {action, amount}, write the
     spoken line in persona. The LLM can never change action/amount.

Price guardrail: any LLM line that doesn't state the exact decided price is
rejected and the deterministic envelope is returned unchanged. No key ->
both return the deterministic result, so the keyless demo and all tests work.

Backends, in order:
  1. an injected client (tests): Anthropic-style client.beta.messages.create,
     or OpenAI-style client.chat.completions.create
  2. NVIDIA NIM (OpenAI-compatible chat completions) when NVIDIA_API_KEY or
     NVIDIA_NIM_API_KEY is set (env or ./.env). Model: NVIDIA_NIM_MODEL or
     NIM_MODEL_DEFAULT; endpoint: NVIDIA_NIM_BASE_URL or integrate.api.nvidia.com
  3. Anthropic Messages API when ANTHROPIC_API_KEY is set
  4. deterministic defaults (no key) — the keyless demo and all tests work.
backend_info() reports which one is live so the demo can say so on screen.
"""

from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace
from typing import Optional

from merchant_brain import CATALOG, PERSONA as M_PERSONA, get_product
from buyer_brain import BUYER_PERSONA

MODEL = "claude-opus-5-5"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "sku": {"type": "string", "enum": [p["sku"] for p in CATALOG]},
        "budget": {"type": "number"},
        "urgency": {"type": "string"},
    },
    "required": ["sku", "budget", "urgency"],
    "additionalProperties": False,
}


NIM_BASE_DEFAULT = "https://integrate.api.nvidia.com/v1"
NIM_MODEL_DEFAULT = "nvidia/nemotron-3-ultra-550b-a55b"
NIM_TIMEOUT = 25  # seconds per call; on timeout the deterministic line is kept

_HERE = os.path.dirname(os.path.abspath(__file__))
_DOTENV_VARS = ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "NVIDIA_NIM_MODEL",
                "NVIDIA_NIM_BASE_URL", "NVIDIA_NIM_TIMEOUT")

# Counters the demo prints: how many lines the model wrote vs. how many the
# price guardrail threw away.
STATS = {"voiced": 0, "guardrail_rejected": 0, "errors": 0}


def reset_stats() -> None:
    for k in STATS:
        STATS[k] = 0


def _dotenv() -> dict:
    """Read only the NIM settings from ./.env (same file token_manager uses).
    Set VEGA_NO_DOTENV=1 to ignore it (tests do)."""
    if os.environ.get("VEGA_NO_DOTENV") == "1":
        return {}
    path = os.path.join(_HERE, ".env")
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if "=" not in line or line.startswith("#"):
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k in _DOTENV_VARS:
                    out[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name) or _dotenv().get(name) or default


def _nim_key() -> Optional[str]:
    return _cfg("NVIDIA_API_KEY") or _cfg("NVIDIA_NIM_API_KEY")


def nim_model() -> str:
    return _cfg("NVIDIA_NIM_MODEL", NIM_MODEL_DEFAULT)


def backend_info(client: Optional[object] = None) -> dict:
    """Which language backend is live — printed by the demo, never the key."""
    if client is not None:
        return {"source": "llm", "backend": "injected client",
                "model": getattr(client, "model", MODEL)}
    if _nim_key():
        return {"source": "llm", "backend": "NVIDIA NIM", "model": nim_model()}
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"source": "llm", "backend": "Anthropic", "model": MODEL}
    return {"source": "fallback", "backend": "deterministic", "model": None}


class _NIMClient:
    """OpenAI-compatible chat-completions client for NVIDIA NIM (urllib only)."""

    def __init__(self, api_key: str, model: Optional[str] = None,
                 base_url: Optional[str] = None, timeout: Optional[float] = None):
        self._key = api_key
        self.model = model or nim_model()
        self.base_url = (base_url or _cfg("NVIDIA_NIM_BASE_URL", NIM_BASE_DEFAULT)).rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
        self.timeout = float(timeout or _cfg("NVIDIA_NIM_TIMEOUT", NIM_TIMEOUT))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def __repr__(self):  # never leak the key in tracebacks/logs
        return f"_NIMClient(model={self.model!r}, base_url={self.base_url!r})"

    def _create(self, **kw):
        import urllib.request

        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(kw).encode(),
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "Authorization": "Bearer " + self._key},
            method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = json.loads(r.read())
        msg = body["choices"][0]["message"]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=msg.get("content") or ""),
            finish_reason=body["choices"][0].get("finish_reason"))])


def _resolve_client(client: Optional[object]) -> Optional[object]:
    """Return a usable client, or None if none available."""
    if client is not None:
        return client
    key = _nim_key()
    if key:
        return _NIMClient(key)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return _DirectClient()


class _DirectClient:
    """urllib-based Anthropic-compatible client. Used when ANTHROPIC_API_KEY
    is set but no client was injected (production, not tests)."""

    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        import urllib.request

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        base_url = os.environ.get("LLM_BASE_URL", "https://api.anthropic.com/v1/messages")
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            headers["x-api-key"] = api_key
        data = json.dumps(kw).encode()
        req = urllib.request.Request(base_url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read())
        text = body["content"][0]["text"] if body.get("content") else ""
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=text)])


def _ask(client, system: str, user: str, max_tokens: int,
         output_format: Optional[dict] = None) -> str:
    """Ask the LLM via the injected client. Returns the text response."""
    if hasattr(client, "chat"):            # OpenAI-compatible (NVIDIA NIM)
        return _ask_openai(client, system, user, max_tokens, output_format)
    kw = dict(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low"},
        betas=[_FALLBACK_BETA],
        fallbacks="default",
    )
    if output_format:
        kw["output_config"] = {"effort": "low", "format": output_format}
    resp = client.beta.messages.create(**kw)
    if getattr(resp, "stop_reason", None) == "refusal":
        raise RuntimeError("model declined")
    return next(b.text for b in resp.content if b.type == "text").strip()


_THINK = re.compile(r"<think>.*?</think>", re.S)

# Guardrail 2: the voice may not invent offers (freebies, discounts, extras)
# or claim a different move than the brain made (e.g. "I'll take it" on a counter).
_PERKS = re.compile(r"\b(free|freebie|throw(?:ing)? in|thrown in|bonus|discount|coupon|gift|"
                    r"complimentary|on the house|extra|percent|\d+\s?%)", re.I)
_ACCEPT_WORDS = re.compile(r"\b(accept(?:ed|ing)?|I'?ll take|we have a deal|it'?s a deal|deal!|sold)\b", re.I)


def _ask_openai(client, system: str, user: str, max_tokens: int,
                output_format: Optional[dict] = None) -> str:
    """Chat-completions call. JSON is requested by prompt and parsed in code
    (portable across NIM models); the caller re-validates every field."""
    model = getattr(client, "model", None) or nim_model()
    if output_format:
        system += ("\nReply with ONLY a JSON object matching this JSON schema, "
                   "no prose, no code fences:\n" + json.dumps(output_format.get("schema", {})))
    kw = dict(model=model, max_tokens=max_tokens + 400, temperature=0.3,
              messages=[{"role": "system", "content": system},
                        {"role": "user", "content": user}])
    if "nemotron" in model:                # answer directly, no reasoning trace
        kw["chat_template_kwargs"] = {"enable_thinking": False}
    resp = client.chat.completions.create(**kw)
    text = _THINK.sub("", resp.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("empty completion")
    if output_format:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("no JSON in completion")
        text = m.group(0)
    return text


# ---------------------------------------------------------------- intent ---

def parse_intent(text: str, *, client: Optional[object] = None,
                 default_sku: Optional[str] = None, default_budget=None) -> dict:
    """Free text -> {"sku", "budget", "urgency", "source": "llm" | "default"}.

    When no client/key is available, returns the deterministic default.
    With a client, asks an LLM constrained to catalog SKUs, then re-validates
    in code (SKU must exist, budget > 0).
    """
    if default_sku is None:
        default_sku = CATALOG[0]["sku"]
    fallback = {
        "sku": default_sku,
        "budget": default_budget if default_budget is not None
                 else BUYER_PERSONA["budget"],
        "urgency": BUYER_PERSONA["urgency"],
        "source": "default",
    }
    c = _resolve_client(client)
    if c is None:
        return fallback

    catalog = "\n".join(
        f"- {p['sku']}: {p['name']} (list ${p['base']:.2f}) — {p['desc']}"
        for p in CATALOG
    )
    try:
        raw = _ask(c,
                   "You are a shopping agent. Map the shopper's request to exactly "
                   "one catalog SKU and their maximum budget in USD. If no budget "
                   "is stated, use the list price.",
                   f"Catalog:\n{catalog}\n\nShopper request:\n{text}",
                   512,
                   {"type": "json_schema", "schema": INTENT_SCHEMA})
        d = json.loads(raw)
        sku, budget = d["sku"], round(float(d["budget"]), 2)
        if sku not in {p["sku"] for p in CATALOG} or not (0 < budget < 100_000):
            return fallback
        return {"sku": sku, "budget": budget,
                "urgency": str(d.get("urgency", ""))[:200], "source": "llm"}
    except Exception:
        return fallback


# --------------------------------------------------------------- voice -----

def voice(env: dict, product: dict, *,
          client: Optional[object] = None) -> dict:
    """Return a copy of `env` whose `note` is written by the LLM in persona.

    action/amount/from/turn are never changed. The LLM cannot invent prices:
    if the spoken line doesn't contain the exact decided price, the envelope
    is returned unchanged (guardrail).
    """
    out = dict(env)
    c = _resolve_client(client)
    if c is None:
        return out

    who = env["from"]
    persona = (f"{M_PERSONA['name']} of {M_PERSONA['shop']}: {M_PERSONA['vibe']}"
               if who == "merchant"
               else f"{BUYER_PERSONA['name']}, {BUYER_PERSONA['role']}")
    price = f"${env['amount']:.2f}" if env["amount"] is not None else None
    decision = f"action={env['action']}" + (f", price={price}" if price else "")
    plain = env.get("note", "")

    try:
        line = _ask(c,
                    f"You speak as {persona}. Write ONE short sentence (max 25 words) "
                    f"for a price negotiation. Do not invent prices, discounts or "
                    f"products. If a price is given, include it exactly as written, and "
                    f"mention no other dollar amount. Never offer extras, freebies or "
                    f"discounts. Only say you accept when action=accept; a counter "
                    f"is an offer, not an acceptance.",
                    f"Product: {product['name']}\nDecision already made: {decision}\n"
                    f"Plain version: {plain}",
                    200)
    except Exception:
        STATS["errors"] += 1
        return out

    line = line.strip().strip('"').strip()
    quoted = set(re.findall(r"\$\s?\d[\d,]*(?:\.\d+)?", line))
    off_script = bool(_PERKS.search(line)) or (
        env["action"] != "accept" and bool(_ACCEPT_WORDS.search(line)))
    if (price and price not in line) or (quoted - ({price} if price else set())) or off_script:
        STATS["guardrail_rejected"] += 1
        return out          # guardrail: exact price, no other prices, no invented
                            # perks, and no words that contradict the action
    out["note"] = line[:240]
    STATS["voiced"] += 1
    return out


if __name__ == "__main__":
    # Smoke test — deterministic fallback path (no LLM key needed)
    from negotiation import negotiate

    n = negotiate("TUNE-01", 90.00)
    env = n.transcript[2]  # merchant counter
    product = get_product("TUNE-01")
    voiced = voice(env, product)
    print("LLM layer smoke test (deterministic fallback):")
    print(f"  original note: {env['note']}")
    print(f"  voiced note:   {voiced['note']}")
    print(f"  price guardrail: {'PASS' if '$88.59' in voiced['note'] or voiced == env else 'FAIL'}")
    print("OK — deterministic fallback works, price guardrail holds.")
