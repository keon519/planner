"""Claude API layer.

Three modules: `client` owns the connection and pricing, `prompts` holds the
system prompts, `tasks` composes them into jobs. Callers import from here and
never need to know which module a name lives in.
"""
from .client import (DEFAULT_PRICE, MODEL, PRICING, _client, _json, _record,
                     _text, price_call)
from .prompts import DECOMPOSE_SYSTEM, EXTRACT_SYSTEM, SUMMARY_SYSTEM
from .tasks import decompose, extract_pdf, extract_text, summarize_class_doc

__all__ = [
    "MODEL", "PRICING", "DEFAULT_PRICE", "price_call",
    "extract_pdf", "extract_text", "decompose", "summarize_class_doc",
    "EXTRACT_SYSTEM", "DECOMPOSE_SYSTEM", "SUMMARY_SYSTEM",
    "_client", "_json", "_text", "_record",
]
