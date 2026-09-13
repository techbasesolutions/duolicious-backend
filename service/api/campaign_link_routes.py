"""GET /s/<key>: count a campaign click and redirect. Read-only for the
member; the only write is the click row, which is why GET is acceptable
here (spec: action links that change member state must POST)."""
from __future__ import annotations

from flask import abort, redirect, request

from database import api_tx
from service.api.decorators import get
from service.campaigns import record_click

@get('/s/<key>')
def get_campaign_link(key: str):
    with api_tx() as tx:
        target = record_click(tx, key, request.headers.get('User-Agent', ''))
    if not target:
        abort(404)
    return redirect(target, code=302)
