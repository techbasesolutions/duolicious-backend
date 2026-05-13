"""
Phase 2 Task 2.4 — outgoing translation preview.

Single endpoint for now:

  POST /translate-preview
    body: {"text": "...", "target": "EN"|"EN-US"|"JA"|...}
    → {"translated": "...", "detected_source": "ES"|null, "cached": bool, "target": "EN-US"}

Calls into the `service.translation_service.translate()` primitive (Task 2.1).
That primitive transparently handles:
  - empty / whitespace passthrough
  - DEEPL_API_KEY-unset passthrough (dev mode just echoes input)
  - Redis cache hit/miss
  - DeepL failure → silent passthrough

Per-user rate-limiting is provided by the global `account_limiter` already
applied via `@apost(...)` in service/api/__init__.py (60/min default), so we
don't add a second limiter here. The text-length cap (500 chars) is enforced
locally to keep DeepL bills predictable for a *preview* endpoint — full chat
messages have a separate path with their own cap (Task 2.2 Step 7, deferred).
"""

from __future__ import annotations

from flask import request

from service.translation_service import translate, normalize_deepl_target

PREVIEW_MAX_CHARS = 500


def post_translate_preview(s):
    """POST /translate-preview handler. `s` is the SessionInfo (auth required)."""
    if not s or not s.person_id:
        return 'Not authorized', 401

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return 'Body must be a JSON object', 400

    text = body.get('text')
    target = body.get('target')

    if not isinstance(text, str):
        return 'text must be a string', 400
    if not isinstance(target, str) or not target.strip():
        return 'target must be a non-empty string', 400

    if len(text) > PREVIEW_MAX_CHARS:
        return f'text exceeds {PREVIEW_MAX_CHARS}-char preview limit', 413

    target_norm = normalize_deepl_target(target)
    result = translate(text, target_norm)

    return {
        'translated':      result.translated,
        'detected_source': result.detected_source,
        'cached':          result.cached,
        'target':          target_norm,
    }
