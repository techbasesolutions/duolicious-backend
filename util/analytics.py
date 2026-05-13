"""
Backend telemetry — Sentry (errors) + PostHog (product events).

Both are opt-in via env vars:
  - SENTRY_DSN              — if unset, Sentry init is skipped
  - POSTHOG_API_KEY         — if unset, PostHog init is skipped
  - POSTHOG_HOST            — defaults to https://us.i.posthog.com
  - SENTRY_TRACES_SAMPLE_RATE — defaults to 0.1 (10%)

Plan reference: Phase 0 Task 0.5. Cross-cutting concern "Telemetry".

PII scrubbing: Sentry's `before_send` strips email + phone fields. PostHog
events are typed (see `Event` discriminated union); no free-form payloads
allowed, so PII can't accidentally land in events.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentry init (opt-in)
# ---------------------------------------------------------------------------

_sentry_initialized = False


def _scrub_pii(event: dict, hint: dict) -> dict | None:
    """Sentry `before_send` hook: strip email + phone from event payloads."""
    SCRUB_KEYS = ('email', 'phone', 'phone_number', 'msisdn')
    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: ('[REDACTED]' if k.lower() in SCRUB_KEYS else scrub(v))
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [scrub(x) for x in node]
        return node
    return scrub(event)


def init_sentry() -> bool:
    """Initialize Sentry if SENTRY_DSN is set. Returns True if active."""
    global _sentry_initialized
    if _sentry_initialized:
        return True
    dsn = os.environ.get('SENTRY_DSN')
    if not dsn:
        logger.info('SENTRY_DSN not set; Sentry disabled (Task 0.5 telemetry)')
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.1')),
            integrations=[FlaskIntegration()],
            before_send=_scrub_pii,
            environment=os.environ.get('DUO_ENV', 'dev'),
            send_default_pii=False,
        )
        _sentry_initialized = True
        logger.info('Sentry initialized')
        return True
    except Exception as e:
        logger.warning(f'Sentry init failed: {e}')
        return False


# ---------------------------------------------------------------------------
# PostHog client + typed event taxonomy
# ---------------------------------------------------------------------------

_posthog_client = None


def _posthog():
    """Lazy PostHog client. Returns None if POSTHOG_API_KEY not set."""
    global _posthog_client
    if _posthog_client is not None:
        return _posthog_client
    api_key = os.environ.get('POSTHOG_API_KEY')
    if not api_key:
        logger.info('POSTHOG_API_KEY not set; PostHog disabled (Task 0.5 telemetry)')
        return None
    try:
        from posthog import Posthog
        _posthog_client = Posthog(
            project_api_key=api_key,
            host=os.environ.get('POSTHOG_HOST', 'https://us.i.posthog.com'),
        )
        logger.info('PostHog client initialized')
        return _posthog_client
    except Exception as e:
        logger.warning(f'PostHog init failed: {e}')
        return None


# Typed event names mirror the frontend taxonomy in `util/analytics.ts`.
# Backend emits a SUBSET — events that originate server-side. Client-side
# events come from the frontend. Keeping the names identical means PostHog
# funnel queries don't have to deduplicate.
EventName = Literal[
    'signup_completed',
    'profile_completed',
    'verification_succeeded',
    'match_created',
    'message_sent',
    'translation_used',
    'paywall_purchase',
    'report_submitted',
    'photo_moderated',
    'account_deleted',
]


class TrackProps(TypedDict, total=False):
    # All optional. Subset that backend events use.
    target_country: str
    primary_language: str
    tier: Literal['bronze', 'silver', 'gold']
    sku: str
    category: str
    moderation_status: Literal['approved', 'rejected', 'manual_review']
    cached: bool


def track(event: EventName, person_uuid: str | None = None,
          props: TrackProps | None = None) -> None:
    """Emit a PostHog event. No-op if PostHog isn't initialized.

    `person_uuid` should be the authed user's UUID (from session). For
    unauthed flows (e.g., signup_completed before session exists), pass None
    and PostHog will use a generated anonymous distinct_id.
    """
    client = _posthog()
    if client is None:
        return
    try:
        client.capture(
            distinct_id=person_uuid or 'anonymous-server',
            event=event,
            properties=dict(props or {}),
        )
    except Exception as e:
        # Telemetry failures must never break the request path.
        logger.warning(f'PostHog track({event}) failed: {e}')
