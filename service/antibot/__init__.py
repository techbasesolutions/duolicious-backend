"""Bot-mitigation primitives for unauthenticated public POSTs.

Two layers:

1. **Honeypot** — a hidden `website` field on each schema. Real users never
   fill it (display:none in CSS + autocomplete=off). Bots that scrape the
   form and submit every field do. The route silently returns a success-
   shaped response so the bot can't tell it was rejected. This is the
   default-on, zero-config layer that already covers ~80% of crude
   scraping bots.

2. **Cloudflare Turnstile** — when `TURNSTILE_SECRET_KEY` is configured,
   the frontend renders a Turnstile widget and submits the token with
   each protected form. The backend POSTs the token to Cloudflare's
   siteverify endpoint and rejects on failure. When the env var is
   blank/unset, verify_turnstile() returns True so dev/staging work
   without keys (zero-config rollout — flip the env var to activate).

Choice rationale (audit Email #2): Turnstile is WCAG 2.1 AAA accessible,
free, unlimited, privacy-first (no third-party tracking), and invisible
by default — no visual challenges unless a user looks suspicious. The
honeypot is the universal-accessibility floor that works even for users
who can't run JS or whose browser blocks Cloudflare.
"""

from __future__ import annotations

import os
from typing import Optional


TURNSTILE_SECRET_KEY: str = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_VERIFY_URL: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def is_honeypot_hit(value: Optional[str]) -> bool:
    """True iff the hidden `website` field carries anything but whitespace.
    Routes should pretend success when this fires (don't leak detection)."""
    return bool(value and value.strip())


def verify_turnstile(token: Optional[str], remote_ip: Optional[str] = None) -> bool:
    """Verify a Cloudflare Turnstile token. Returns True iff the token is
    valid OR if no secret is configured (zero-config rollout). Never raises;
    on network/Cloudflare error returns False so the caller can decide
    fail-open vs fail-closed for that specific route."""
    if not TURNSTILE_SECRET_KEY:
        # Feature flag off — allow everything. Honeypot is still active.
        return True
    if not token:
        return False
    try:
        import requests
        resp = requests.post(
            TURNSTILE_VERIFY_URL,
            data={
                "secret": TURNSTILE_SECRET_KEY,
                "response": token,
                **({"remoteip": remote_ip} if remote_ip else {}),
            },
            timeout=5,
        )
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("success"))
    except Exception as e:
        print(f"verify_turnstile: exception {e!r}; failing closed")
        return False
