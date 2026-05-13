"""
Phase 2 — translation primitive.

Wraps DeepL's REST API + a Redis hash cache. Both are opt-in:

  - DEEPL_API_KEY    — if unset, `translate()` becomes a pass-through
                       (returns original text + None detected_source).
                       This lets the rest of the pipeline (mark-read +
                       JSONB persistence) keep working in dev without
                       a paid DeepL account.
  - REDIS_URL        — if unset, falls back to the duolicious-default
                       `redis://redis:6379` (the docker-compose redis service).

Cache key: SHA-256 of `(target_lang_normalized, text)`. Source is auto-detected
by DeepL on the first call and stored alongside the translated text in a
Redis hash so subsequent identical requests don't re-detect.

DeepL target-lang normalization: DeepL rejects bare 'EN' and 'PT' as targets
and requires regional variants. `normalize_deepl_target('EN')` → 'EN-US',
`'PT'` → 'PT-PT'. Most other languages pass through unchanged.

Phase 2 Task 2.2 builds `translate_messages_for(messages, target_lang)` on
top of `translate()`, mutating each message row in-place to fill the
`detected_source_lang` + `translations` JSONB columns added by migration
0002. Wiring into the chat-service's mark-read path is deferred until the
chat process's persistence layer is fully understood + DEEPL_API_KEY is
provided by the user.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_TTL = 60 * 60 * 24 * 30   # 30 days
CACHE_PREFIX = 'tr:'

# DeepL target-lang regional defaults. Bare codes outside this map pass through.
DEEPL_TARGET_OVERRIDES = {
    'EN': 'EN-US',   # default to US English; per-user setting could override
    'PT': 'PT-PT',   # default to European Portuguese
}


@dataclass
class TranslationResult:
    translated: str
    detected_source: Optional[str]  # ISO-639-1 (e.g., 'EN', 'JA') or None
    cached: bool


# ---------------------------------------------------------------------------
# Lazy clients (only initialized when used + creds present)
# ---------------------------------------------------------------------------

_deepl_client = None
_deepl_init_attempted = False
_redis_client = None
_redis_init_attempted = False


def _deepl():
    """Returns the DeepL Translator instance, or None if unavailable."""
    global _deepl_client, _deepl_init_attempted
    if _deepl_init_attempted:
        return _deepl_client
    _deepl_init_attempted = True

    api_key = os.environ.get('DEEPL_API_KEY')
    if not api_key:
        logger.info('DEEPL_API_KEY not set; translate() will pass-through')
        return None
    try:
        import deepl
        _deepl_client = deepl.Translator(api_key)
        logger.info('DeepL client initialized')
    except Exception as e:
        logger.warning(f'DeepL init failed: {e}')
        _deepl_client = None
    return _deepl_client


def _redis():
    """Returns the Redis client, or None if unavailable."""
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    _redis_init_attempted = True

    try:
        from redis import Redis
        url = os.environ.get('REDIS_URL', 'redis://redis:6379')
        _redis_client = Redis.from_url(url)
        # Lazy probe — don't fail if redis is down at import time.
        _redis_client.ping()
        logger.info(f'Redis cache wired at {url}')
    except Exception as e:
        logger.warning(f'Redis init failed (translation cache disabled): {e}')
        _redis_client = None
    return _redis_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_deepl_target(target: str) -> str:
    """Maps a bare ISO-639-1 code to DeepL's required regional variant.

    >>> normalize_deepl_target('EN')
    'EN-US'
    >>> normalize_deepl_target('en')
    'EN-US'
    >>> normalize_deepl_target('PT')
    'PT-PT'
    >>> normalize_deepl_target('JA')
    'JA'
    >>> normalize_deepl_target('en-gb')
    'EN-GB'
    """
    t = target.upper()
    return DEEPL_TARGET_OVERRIDES.get(t, t)


def cache_key(text: str, target: str) -> str:
    """SHA-256 cache key. Keyed on (target, text) only — source is auto-detected
    and stored as part of the cache value, so a caller passing source=None and
    another passing source='ES' both hit the same cache entry."""
    digest = hashlib.sha256(f'{target.upper()}|{text}'.encode('utf-8')).hexdigest()
    return f'{CACHE_PREFIX}{digest}'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def translate(text: str, target: str) -> TranslationResult:
    """Translate `text` into `target` (DeepL convention; will be normalized).

    Returns the original text + None detected_source if either:
      - text is empty/whitespace-only
      - DEEPL_API_KEY is unset (dev pass-through)
      - DeepL itself is unreachable

    Telemetry: `translation_used` PostHog event fires (when telemetry on)
    via the caller, not this primitive.
    """
    if not text or not text.strip():
        return TranslationResult(translated=text, detected_source=None, cached=False)

    target_norm = normalize_deepl_target(target)
    k = cache_key(text, target_norm)

    # 1. Try cache
    r = _redis()
    if r is not None:
        try:
            cached = r.hgetall(k)
            if cached:
                # Redis returns bytes; decode.
                raw_text = cached.get(b'text')
                raw_src = cached.get(b'source')
                if raw_text:
                    return TranslationResult(
                        translated=raw_text.decode('utf-8'),
                        detected_source=raw_src.decode('utf-8') if raw_src else None,
                        cached=True,
                    )
        except Exception as e:
            # Cache read failure must never block the request path.
            logger.warning(f'Translation cache read failed: {e}')

    # 2. Call DeepL (or pass through if no key)
    client = _deepl()
    if client is None:
        return TranslationResult(translated=text, detected_source=None, cached=False)

    try:
        result = client.translate_text(text, target_lang=target_norm)
        translated = str(result)
        detected = getattr(result, 'detected_source_lang', None)
    except Exception as e:
        logger.warning(f'DeepL translate_text failed for target={target_norm}: {e}')
        return TranslationResult(translated=text, detected_source=None, cached=False)

    # 3. Write-through cache
    if r is not None:
        try:
            r.hset(k, mapping={
                'text': translated,
                'source': detected or '',
            })
            r.expire(k, CACHE_TTL)
        except Exception as e:
            logger.warning(f'Translation cache write failed: {e}')

    return TranslationResult(
        translated=translated,
        detected_source=detected,
        cached=False,
    )


def translate_messages_for(messages: list, target_lang: str) -> list:
    """Phase 2 Task 2.2 helper. Mutates each message in-place: fills
    `detected_source_lang` and `translations[target_lang_normalized]`.

    Skips messages where:
      - text is empty
      - target translation is already present on the row
      - source matches target language family

    Each message is expected to expose attributes/items:
      - .text         (str)
      - .detected_source_lang  (Optional[str])
      - .translations (dict, mutable; gets the new key written in)

    Caller is responsible for committing the row updates back to the DB
    after this returns.
    """
    target_norm = normalize_deepl_target(target_lang)
    target_family = target_norm.split('-')[0]   # 'EN-US' -> 'EN'

    for m in messages:
        if not getattr(m, 'text', None) or not m.text.strip():
            continue

        existing = getattr(m, 'translations', None) or {}

        # Already translated to this target?
        if target_norm in existing:
            continue

        # Already known to be in the same language as target?
        existing_src = getattr(m, 'detected_source_lang', None)
        if existing_src and existing_src.upper() == target_family:
            continue

        result = translate(m.text, target=target_norm)

        # Backfill detected_source_lang on the row
        if not existing_src and result.detected_source:
            m.detected_source_lang = result.detected_source.upper()[:2]

        # Skip if DeepL's detection matches target family
        new_src = getattr(m, 'detected_source_lang', None)
        if new_src and new_src.upper() == target_family:
            continue

        m.translations = {**existing, target_norm: result.translated}

    return messages
