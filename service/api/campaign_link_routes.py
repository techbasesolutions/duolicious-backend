"""GET /s/<key>: count a campaign click and redirect. Read-only for the
member; the only write is the click row, which is why GET is acceptable
here (spec: action links that change member state must POST).

F10: the click's receipt is handed back to the web forwarder in the
X-Spotlight-Receipt response header, never as a cookie -- the web app owns
the cookie, this service only mints the value that goes in it. An optional
`?p=facebook|instagram` names the platform the click came from; anything
else is dropped by `record_click` rather than trusted into the column."""
from __future__ import annotations

from flask import abort, redirect, request

from database import api_tx
from service.api.decorators import get
from service.campaigns import record_click

@get('/s/<key>')
def get_campaign_link(key: str):
    with api_tx() as tx:
        target, receipt = record_click(
            tx, key, request.headers.get('User-Agent', ''),
            platform=request.args.get('p'))
    if not target:
        abort(404)
    response = redirect(target, code=302)
    if receipt:
        response.headers['X-Spotlight-Receipt'] = receipt
    return response
