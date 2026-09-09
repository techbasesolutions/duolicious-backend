"""
Meta Conversions API — server-side ad-conversion events.

The browser-side Meta Pixel (ahavah-web, docs/meta-pixel-plan.md) fires
CompleteRegistration with event_id `reg-<person_uuid>`; this module sends
the same event server-side with the same event_id so Meta deduplicates
the pair and keeps the better-attributed copy. Server events survive ad
blockers and iOS tracking prevention, which drop 30-60% of browser-only
conversions.

Opt-in via AHAVAH_META_PIXEL_ID + AHAVAH_META_CAPI_ACCESS_TOKEN (both
empty by default — see service/config.py). Sends are fire-and-forget on
a daemon thread: a Meta outage must never fail or slow /finish-onboarding.

Uses stdlib urllib on purpose — no requests/httpx in requirements.txt,
and one POST per registration does not justify a new dependency.
"""

from __future__ import annotations

import hashlib
import os
import json
import threading
import time
import traceback
import urllib.request

from service.config import META_CAPI_ACCESS_TOKEN, META_PIXEL_ID, WEB_BASE_URL

_GRAPH_URL = f"https://graph.facebook.com/v23.0/{META_PIXEL_ID}/events"


def _hash_email(email: str) -> str:
    # Meta requires sha256 of the trimmed, lowercased address.
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _post(payload: dict) -> None:
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{_GRAPH_URL}?access_token={META_CAPI_ACCESS_TOKEN}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception:
        # Conversion telemetry is best-effort; log and move on.
        print("metapixel: CompleteRegistration send failed")
        traceback.print_exc()


def send_complete_registration(
    email: str,
    person_uuid: str,
    client_ip: str | None,
    client_user_agent: str | None,
) -> None:
    """Fire-and-forget. Call AFTER the registration transaction commits."""
    # Member registration is sensitive. Existing credentials alone are not
    # authorization to export it; an explicit telemetry policy must enable it.
    if os.environ.get('AHAVAH_REGISTRATION_TELEMETRY') != 'enabled':
        return
    if not META_PIXEL_ID or not META_CAPI_ACCESS_TOKEN:
        return

    user_data: dict = {"em": [_hash_email(email)]}
    # IP + UA raise Meta's match quality; both must be the END USER's
    # values (never the server's), so they're captured from the Flask
    # request in the handler and passed in.
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if client_user_agent:
        user_data["client_user_agent"] = client_user_agent

    payload = {
        "data": [
            {
                "event_name": "CompleteRegistration",
                "event_time": int(time.time()),
                # Deterministic: dedupes against the browser pixel's copy
                # AND against accidental repeat sends for the same person.
                "event_id": hashlib.sha256(f"registration:{person_uuid}".encode()).hexdigest(),
                "action_source": "website",
                "event_source_url": f"{WEB_BASE_URL}/onboarding/complete",
                "user_data": user_data,
            }
        ]
    }

    threading.Thread(target=_post, args=(payload,), daemon=True).start()
