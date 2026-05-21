"""On-demand chat translation via OpenAI. Never errors the chat: every
failure path (no key, OpenAI/Redis down, over length) passes the original
text through. Redis-cached by (target, md5(text)).
"""
from __future__ import annotations

import hashlib
import os

MAX_CHARS = 1000
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days
_MODEL = os.environ.get("OPENAI_TRANSLATE_MODEL", "gpt-4.1-mini-2025-04-14")

_REDIS_HOST = os.environ.get("DUO_REDIS_HOST", "redis")
_REDIS_PORT = int(os.environ.get("DUO_REDIS_PORT", 6379))
_redis_client = None


def _redis():
    """Lazily build a sync redis client; None if redis is unavailable."""
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            _redis_client = redis.Redis(
                host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True
            )
        except Exception:
            return None
    return _redis_client


def _cache_key(text: str, target: str) -> str:
    return f"tx:{target}:{hashlib.md5(text.encode('utf-8')).hexdigest()}"


def _openai_translate(text: str, target: str, model: str) -> str:
    """One sync OpenAI chat call. Returns the translation text."""
    from openai import OpenAI
    client = OpenAI()  # reads OPENAI_API_KEY
    resp = client.chat.completions.create(
        model=model,
        temperature=0.0,
        messages=[
            {
                "role": "system",
                "content": (
                    f"Translate the user's message to {target}. Return ONLY "
                    f"the translation, with no quotes, labels, or notes. If it "
                    f"is already in {target}, return it unchanged."
                ),
            },
            {"role": "user", "content": text},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def translate(text: str, target: str) -> dict:
    """Returns {translated, detected_source, cached}. Passthrough on any
    failure. Raises ValueError only for over-length (route maps to 413)."""
    if not text or not text.strip():
        return {"translated": text, "detected_source": None, "cached": False}
    if len(text) > MAX_CHARS:
        raise ValueError("too_long")
    if not os.environ.get("OPENAI_API_KEY"):
        return {"translated": text, "detected_source": None, "cached": False}

    r = _redis()
    key = _cache_key(text, target)
    if r is not None:
        try:
            hit = r.get(key)
            if hit is not None:
                return {"translated": hit, "detected_source": None, "cached": True}
        except Exception:
            pass

    try:
        translated = _openai_translate(text, target, _MODEL)
    except Exception:
        return {"translated": text, "detected_source": None, "cached": False}

    if r is not None and translated:
        try:
            r.setex(key, _CACHE_TTL, translated)
        except Exception:
            pass

    return {"translated": translated or text, "detected_source": None, "cached": False}
