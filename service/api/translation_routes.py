"""Authed POST /translate - on-demand chat message translation.

Sibling module (imported at the bottom of service/api/__init__.py) to avoid
the brittle top-level import. Translation itself never errors the chat
(service.translation passes through on failure); this layer only validates
the request shape + length.
"""
from __future__ import annotations

from flask import request

import duotypes as t
from service.api.decorators import apost
from service.translation import translate, MAX_CHARS


@apost('/translate')
def post_translate(s: t.SessionInfo):
    assert s.person_id is not None
    body = request.get_json(silent=True) or {}
    text = body.get('text')
    target = body.get('target')
    if not isinstance(text, str) or not isinstance(target, str) or not target.strip():
        return {'error': 'bad_request'}, 400
    if len(text) > MAX_CHARS:
        return {'error': 'too_long'}, 413
    return translate(text, target), 200
