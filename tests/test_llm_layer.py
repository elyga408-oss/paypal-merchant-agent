"""llm_layer with a fake Anthropic client — no network, no key."""
import json
from types import SimpleNamespace

import llm_layer
from merchant_brain import get_product


class FakeClient:
    def __init__(self, text, stop_reason="end_turn"):
        self.calls = []
        self._text, self._stop = text, stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self._stop,
                               content=[SimpleNamespace(type="text", text=self._text)])


ENV = {"from": "merchant", "action": "counter", "amount": 88.59,
       "note": "Hand-tuned work, I can do $88.59.", "turn": 2}


def test_no_key_means_deterministic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    assert llm_layer.backend_info()["source"] == "fallback"
    assert llm_layer.voice(ENV, get_product("TUNE-01")) == ENV
    assert llm_layer.parse_intent("tune my bike")["source"] == "default"


def test_voice_replaces_only_note():
    c = FakeClient("For a same-week hand tune, $88.59 is where I can land.")
    out = llm_layer.voice(ENV, get_product("TUNE-01"), client=c)
    assert out["note"].startswith("For a same-week")
    assert {k: v for k, v in out.items() if k != "note"} == {k: v for k, v in ENV.items() if k != "note"}
    kw = c.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"


def test_voice_rejects_line_with_wrong_price():
    c = FakeClient("I can do $80.00 for you today!")
    assert llm_layer.voice(ENV, get_product("TUNE-01"), client=c) == ENV


def test_voice_refusal_falls_back():
    c = FakeClient("", stop_reason="refusal")
    assert llm_layer.voice(ENV, get_product("TUNE-01"), client=c) == ENV


def test_parse_intent_valid():
    c = FakeClient(json.dumps({"sku": "LAMP-03", "budget": 40, "urgency": "night rides"}))
    got = llm_layer.parse_intent("need a bright light, $40 max", client=c)
    assert got == {"sku": "LAMP-03", "budget": 40.0, "urgency": "night rides", "source": "llm"}
    assert c.calls[0]["output_config"]["format"]["type"] == "json_schema"


def test_parse_intent_rejects_unknown_sku_and_garbage():
    for text in (json.dumps({"sku": "BIKE-99", "budget": 40, "urgency": ""}),
                 json.dumps({"sku": "LAMP-03", "budget": -5, "urgency": ""}),
                 "not json"):
        assert llm_layer.parse_intent("x", client=FakeClient(text))["source"] == "default"


# ------------------------------------------------- NVIDIA NIM (OpenAI-style) ---

class FakeNIM:
    """Mocked OpenAI-compatible client: client.chat.completions.create."""
    model = "nvidia/test-model"

    def __init__(self, text):
        self.calls, self._text = [], text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self._text), finish_reason="stop")])


def test_nim_selected_when_key_present(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake-not-real")
    c = llm_layer._resolve_client(None)
    assert isinstance(c, llm_layer._NIMClient)
    assert "nvapi" not in repr(c)
    info = llm_layer.backend_info()
    assert info["source"] == "llm" and info["backend"] == "NVIDIA NIM"
    assert info["model"] == llm_layer.NIM_MODEL_DEFAULT


def test_nim_voice_keeps_action_and_amount():
    c = FakeNIM("<think>plan</think>Honest hands-on work, and $88.59 is my best for you.")
    out = llm_layer.voice(ENV, get_product("TUNE-01"), client=c)
    assert out["note"] == "Honest hands-on work, and $88.59 is my best for you."
    assert {k: v for k, v in out.items() if k != "note"} == {k: v for k, v in ENV.items() if k != "note"}
    kw = c.calls[0]
    assert kw["model"] == "nvidia/test-model"
    assert kw["messages"][0]["role"] == "system"


def test_nim_guardrail_rejects_missing_or_extra_prices():
    p = get_product("TUNE-01")
    for bad in ("I can do $80.00 for you today!",           # wrong price
                "Usually $95.00, but for you $88.59.",      # extra price
                "Let's make a deal, friend."):              # price missing
        assert llm_layer.voice(ENV, p, client=FakeNIM(bad)) == ENV


def test_nim_parse_intent_prompt_and_parse():
    c = FakeNIM('Sure! {"sku": "TUNE-01", "budget": 90, "urgency": "race on Sunday"}')
    got = llm_layer.parse_intent("tune-up before Sunday, about $90", client=c)
    assert got == {"sku": "TUNE-01", "budget": 90.0, "urgency": "race on Sunday", "source": "llm"}
    assert "JSON" in c.calls[0]["messages"][0]["content"]
    assert llm_layer.parse_intent("x", client=FakeNIM("no json here"))["source"] == "default"


def test_nim_client_http_is_mocked(monkeypatch):
    seen = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok $88.59"},
                                            "finish_reason": "stop"}]}).encode()

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["timeout"] = req.full_url, timeout
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data)
        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = llm_layer._NIMClient("fake-key", model="m1", base_url="https://nim.example/v1",
                             timeout=7)
    r = c.chat.completions.create(model="m1", messages=[], max_tokens=5)
    assert r.choices[0].message.content == "ok $88.59"
    assert seen == {"url": "https://nim.example/v1/chat/completions", "timeout": 7.0,
                    "auth": "Bearer fake-key",
                    "body": {"model": "m1", "messages": [], "max_tokens": 5}}


def test_nim_guardrail_rejects_invented_perks_and_wrong_move():
    p = get_product("TUNE-01")
    for bad in ("I can't go below $88.59, but I'll throw in a free bottle cage.",
                "$88.59 and 10% off your next visit.",
                "I'll take it at $88.59."):                 # ENV is a counter, not an accept
        assert llm_layer.voice(ENV, p, client=FakeNIM(bad)) == ENV
    acc = dict(ENV, action="accept", **{"from": "buyer"})
    out = llm_layer.voice(acc, p, client=FakeNIM("Deal accepted at $88.59."))
    assert out["note"] == "Deal accepted at $88.59."
