"""
Ahavah backend — environment-driven URL configuration.

This module centralises the small handful of public-facing base URLs that
need to differ between dev, staging, and production deployments. Every
hardcoded URL that previously lived in the upstream Duolicious fork is
now read from an `AHAVAH_*` environment variable with a sensible dev
default so a fresh `docker compose up` still works without extra setup.

Internal identifiers (Python env vars like `DUO_ENV`, Postgres tables
prefixed `duo_*`, internal function names containing `duo`) are
intentionally left as-is — they are never user-visible and renaming them
would force schema migrations + breakage with zero user-facing benefit.
See README.md "Fork heritage" for the rationale.

Read at module-import time. The values are plain strings, not objects,
so consumers can either `from service import config` and reach
`config.API_BASE_URL`, or `from service.config import API_BASE_URL` for
a once-resolved snapshot.
"""

from __future__ import annotations

import os


# Public REST API base URL — used in moderator/abuse emails that embed
# ban/delete links that resolve to admin endpoints on this service.
API_BASE_URL: str = os.environ.get(
    "AHAVAH_API_BASE_URL",
    "http://localhost:5000",
)


# Public web-app base URL — used in transactional emails ("Open Ahavah",
# "Get on Ahavah" CTA buttons) so links route to the PWA, not the API.
WEB_BASE_URL: str = os.environ.get(
    "AHAVAH_WEB_BASE_URL",
    "http://localhost:3000",
)


# Email-asset CDN base URL — used for `<img src=…>` in HTML email
# templates (header logo, decorative imagery). Static assets, separate
# from user-uploaded content.
EMAIL_ASSETS_BASE_URL: str = os.environ.get(
    "AHAVAH_EMAIL_ASSETS_BASE_URL",
    "https://email-assets.ahavah.app",
)


# User-uploaded image CDN base URL — used in moderator emails (photo
# review links) and in SQL queries that build photo_url columns for
# export / moderation. Distinct from the email-assets CDN because user
# images are private + UUID-addressed.
USER_IMAGES_BASE_URL: str = os.environ.get(
    "AHAVAH_USER_IMAGES_BASE_URL",
    "https://user-images.ahavah.app",
)


# XMPP server local domain (LSERVER in MongooseIM terminology). Used in
# JIDs of the form `<uuid>@<domain>` when building XML stanzas from
# Python before they hand off to the chat service.
XMPP_DOMAIN: str = os.environ.get(
    "AHAVAH_XMPP_DOMAIN",
    "ahavah.app",
)


# User-visible product name. Used in email subjects, body copy, and the
# SMTP From: display name. Keep this consistent with the web app's
# branding.
PRODUCT_NAME: str = os.environ.get(
    "AHAVAH_PRODUCT_NAME",
    "Ahavah",
)


# User-visible email domain. Used to build default from-addresses
# (`noreply-otp@<domain>`, `support@<domain>`, `no-reply@<domain>`).
EMAIL_DOMAIN: str = os.environ.get(
    "AHAVAH_EMAIL_DOMAIN",
    "ahavah.app",
)


# Pre-launch signup gate. Until launch, /request-otp is closed to the public so
# no account can be created before the allotted time. Defaults to CLOSED; flip
# AHAVAH_SIGNUPS_OPEN=true at launch. SIGNUP_ALLOWED_DOMAINS is a comma-separated
# list of email domains (e.g. the team's own domain) that bypass the gate to
# test sign-in via the API.
SIGNUPS_OPEN: bool = os.environ.get("AHAVAH_SIGNUPS_OPEN", "false").strip().lower() == "true"
SIGNUP_ALLOWED_DOMAINS: frozenset = frozenset(
    d.strip().lower().lstrip("@")
    for d in os.environ.get("AHAVAH_SIGNUP_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
)
