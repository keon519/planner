import json
"""Claude API client, token pricing, and response parsing."""
from anthropic import Anthropic

from .. import config


MODEL = "claude-sonnet-4-6"


PRICING = {
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5":  {"in": 1.00, "out":  5.00},
    "claude-opus-4-1":   {"in": 15.00, "out": 75.00},
}


DEFAULT_PRICE = {"in": 3.00, "out": 15.00}


def price_call(model: str, usage) -> dict:
    """Turn an API usage block into token counts and an estimated cost."""
    p = PRICING.get(model, DEFAULT_PRICE)
    tin = getattr(usage, "input_tokens", 0) or 0
    tout = getattr(usage, "output_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = (tin * p["in"] + cw * p["in"] * 1.25 + cr * p["in"] * 0.10
            + tout * p["out"]) / 1_000_000
    return {"model": model, "input_tokens": tin, "output_tokens": tout,
            "cache_write": cw, "cache_read": cr, "cost_usd": round(cost, 6)}


def _record(sink, kind, resp):
    if sink is None:
        return
    try:
        sink.append({"kind": kind, **price_call(resp.model, resp.usage)})
    except Exception:
        pass


def _client() -> Anthropic:
    key = config.ANTHROPIC_API_KEY
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return Anthropic(api_key=key)


def _json(text: str):
    """Models sometimes wrap JSON in fences despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(t)


def _text(resp) -> str:
    return "".join(b.text for b in resp.content if b.type == "text")
