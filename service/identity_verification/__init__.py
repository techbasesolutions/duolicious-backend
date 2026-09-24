"""
Phase 3 Task 3.1 — verification-tier promotion (Stripe Identity webhook +
session-creation endpoint).

Three tiers (per migration 0003):
  bronze : the upstream Duolicious fork's existing selfie+gender+age+ethnicity check (set
           elsewhere by service.person.post_verify when the legacy flow
           passes — this module does NOT touch bronze).
  silver : AWS Amplify Face Liveness (Phase 3 Task 3.2 Step 3 — separate
           webhook flow, not implemented in this module).
  gold   : Stripe Identity (this module).

This module is intentionally side-effect-free at import time. Stripe is
configured lazily on first use so the api can boot in dev without
STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET set — the routes will just
reject calls with a 503 in that case (the same opt-in pattern as the
DeepL primitive in Phase 2).

Endpoints exposed via service/api/__init__.py:
  POST /verification/start-id-flow
       → creates a Stripe Identity VerificationSession for the caller,
         returns {"client_secret": "vs_..."} for the frontend to open.
  POST /webhooks/stripe-identity
       → Stripe-signed webhook. On `verification_session.verified`,
         promotes the linked person to verification_level='gold' and
         records the issuing country + verification timestamp. Every other
         session outcome is recorded against the same person (migration
         0054), and `requires_input` also tells the member their check
         needs another try.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from flask import request

from database import api_tx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy Stripe configuration
# ---------------------------------------------------------------------------

_stripe_init_attempted = False
_stripe_module = None
_webhook_secret: Optional[str] = None


def _stripe():
    """Returns the configured stripe module, or None if STRIPE_SECRET_KEY is unset."""
    global _stripe_init_attempted, _stripe_module, _webhook_secret
    if _stripe_init_attempted:
        return _stripe_module
    _stripe_init_attempted = True

    api_key = os.environ.get('STRIPE_SECRET_KEY')
    if not api_key:
        logger.info('STRIPE_SECRET_KEY not set; Stripe Identity flow disabled')
        return None

    try:
        import stripe
        stripe.api_key = api_key
        _stripe_module = stripe
        # Suffix matches the checkout module's `STRIPE_WEBHOOK_SECRET_CHECKOUT`
        # convention. Falling back to the un-suffixed name keeps any older
        # deploys booting if both are set / only the legacy name is set.
        _webhook_secret = (
            os.environ.get('STRIPE_WEBHOOK_SECRET_IDENTITY')
            or os.environ.get('STRIPE_WEBHOOK_SECRET')
        )
        if not _webhook_secret:
            logger.warning(
                'STRIPE_SECRET_KEY set but STRIPE_WEBHOOK_SECRET_IDENTITY '
                'unset; incoming Identity webhooks will be rejected'
            )
        logger.info('Stripe Identity client initialized')
    except Exception as e:
        logger.warning(f'Stripe init failed: {e}')
        _stripe_module = None
    return _stripe_module


# ---------------------------------------------------------------------------
# Promotion helper (pure DB — testable without Stripe)
# ---------------------------------------------------------------------------

VALID_LEVELS = {'none', 'bronze', 'silver', 'gold'}


def promote_user(person_id: int, level: str, country: Optional[str] = None) -> bool:
    """Sets verification_level on `person_id`. Idempotent — re-promoting to
    the same level is a no-op. Promoting *down* is rejected (returns False),
    so a user who's already gold can't be silently demoted by a stale silver
    webhook arriving after a gold one.

    Returns True if the row was updated, False otherwise (already at or
    above the requested level, or person not found).
    """
    if level not in VALID_LEVELS:
        raise ValueError(f'level must be one of {VALID_LEVELS}, got {level!r}')

    rank = {'none': 0, 'bronze': 1, 'silver': 2, 'gold': 3}
    new_rank = rank[level]

    with api_tx() as tx:
        row = tx.execute(
            'SELECT ahavah_verification_tier FROM person WHERE id = %(id)s',
            dict(id=person_id),
        ).fetchone()
        if not row:
            return False

        # Migration 0003 added `ahavah_verification_tier` ENUM
        # ('none','bronze','silver','gold') as the Phase W tier column.
        # The earlier code addressed `verification_level` (text), which
        # never existed — every webhook silently 500'd. The legacy
        # `verification_level_id` (integer FK to the upstream
        # Duolicious lookup) is updated separately by the Bronze cron
        # at service/cron/verificationjobrunner.
        current = row['ahavah_verification_tier']
        if rank.get(current, 0) >= new_rank:
            # Don't allow demotion or no-op rewrites.
            return False

        if level == 'gold':
            # The four latch columns are cleared in the same statement. They
            # describe attempts that granted nothing, and a member who
            # failed one check and passed the next would otherwise hold gold
            # while the latch still read `requires_input`. Nothing consumes
            # the column yet, so today it could only mislead an operator;
            # the first consumer would read it naively and tell a verified
            # member to go and try again, which is the exact class of lie
            # this wave exists to remove. record_identity_outcome refuses to
            # write once a member is gold, so nothing puts them back.
            tx.execute(
                """
                UPDATE person
                   SET ahavah_verification_tier   = %(level)s::ahavah_verification_tier,
                       id_verified_country        = %(country)s,
                       id_verified_at             = NOW(),
                       id_verification_status     = NULL,
                       id_verification_status_at  = NULL,
                       id_verification_session_id = NULL,
                       id_verification_error_code = NULL
                 WHERE id = %(id)s
                """,
                dict(id=person_id, level=level, country=country),
            )
        else:
            tx.execute(
                """
                UPDATE person
                   SET ahavah_verification_tier = %(level)s::ahavah_verification_tier
                 WHERE id = %(id)s
                """,
                dict(id=person_id, level=level),
            )

    # Notify on a Gold (Stripe Identity) approval. Bronze/silver are
    # finalized + notified by service/cron/verificationjobrunner, so we only
    # fire here for gold to avoid double-notifying. Fire-and-forget.
    if level == 'gold':
        try:
            from service.notifications import notify
            from emails.notification import new_verification_email
            notify(
                person_id, "verification",
                title="You're verified",
                body="Your Gold verification was approved.",
                url="/verify",
                email_subject="You're verified on Ahavah",
                email_html_factory=lambda unsub: new_verification_email("Gold", unsub),
            )
        except Exception:
            import traceback
            print("identity_verification notify failed:")
            print(traceback.format_exc())

    return True


# ---------------------------------------------------------------------------
# The outcomes that are not a promotion
# ---------------------------------------------------------------------------

# Stripe event type -> the outcome we store. `verified` is absent on
# purpose: a success is recorded by ahavah_verification_tier and
# id_verified_country / id_verified_at, and writing it here as well would
# give us two places that can disagree about the same fact. This latch
# describes the attempts that granted nothing.
NON_VERIFIED_OUTCOMES = {
    'identity.verification_session.created':        'created',
    'identity.verification_session.processing':     'processing',
    'identity.verification_session.requires_input': 'requires_input',
    'identity.verification_session.canceled':       'canceled',
}

# The one the member can act on. `canceled` is written and not notified:
# the member cancelled, so they already know. `created` and `processing`
# are not news either, and they are stored so that a member who starts a
# new check is not left carrying last week's `requires_input`.
NOTIFIED_OUTCOMES = {'requires_input'}


def record_identity_outcome(
    person_id: int,
    status: str,
    *,
    session_id: Optional[str] = None,
    error_code: Optional[str] = None,
    event_at: Optional[datetime] = None,
) -> bool:
    """Stores the last thing Stripe told us about this member's ID check.

    Returns True if this was NEWS: a member who exists, who is not already
    gold, whose stored outcome this event actually changes, and whose
    stored outcome is not already newer than this event. False for anything
    else, including a redelivery of an event we have already recorded.

    That definition is load bearing. Stripe's webhook delivery is at least
    once, it documents that an event may arrive more than once, and this
    route deliberately answers 500 on a failed write so that Stripe retries
    it. An unconditional write reported True on every one of those
    deliveries, and the caller notifies on True, so one blurry document
    produced "Your ID check needs another try" as many times as Stripe felt
    like sending the event. Letting Stripe retry is only safe when the
    retry is idempotent from the member's point of view, so the write
    reports a transition rather than a write.

    Three guards, all in the one statement:

      * gold is terminal. Stripe redelivers and can deliver out of order,
        so without this a late `requires_input` would tell a member who is
        already verified to go and try again.
      * the outcome has to differ. Same status and same session is the same
        news, however many times it arrives.
      * the stored outcome must not already be newer. Stripe does not
        guarantee ordering, so a late `created` or `processing` could
        otherwise overwrite a `requires_input` that is the actionable
        truth. The member has already been told at that point, so nothing
        lies to them, but the operator record would be wrong.

    `event_at` is the event's own `created` time, not the time we received
    it, because that is the only ordering Stripe gives us. Unset falls back
    to NOW(), which is at or after anything already stored and so never
    blocks a write on its own.

    The tier is never read or changed here: this function only ever writes
    these four columns. `error_code` is overwritten on every outcome,
    including with None. A code left over from the previous attempt would
    send an operator after the wrong thing.
    """
    with api_tx() as tx:
        row = tx.execute(
            """
            UPDATE person
               SET id_verification_status     = %(status)s,
                   id_verification_status_at  = COALESCE(
                       %(event_at)s::TIMESTAMPTZ, NOW()),
                   id_verification_session_id = %(session_id)s,
                   id_verification_error_code = %(error_code)s
             WHERE id = %(id)s
               AND ahavah_verification_tier <> 'gold'
               AND (
                   id_verification_status     IS DISTINCT FROM %(status)s
                OR id_verification_session_id IS DISTINCT FROM %(session_id)s
               )
               AND (
                   id_verification_status_at IS NULL
                OR id_verification_status_at <= COALESCE(
                       %(event_at)s::TIMESTAMPTZ, NOW())
               )
            RETURNING id
            """,
            dict(id=person_id, status=status, session_id=session_id,
                 error_code=error_code, event_at=event_at),
        ).fetchone()
    return row is not None


def _notify_id_check_needs_another_try(person_id: int) -> None:
    """Tells the member their ID check needs another try, through the same
    notify() path and the same `verification` preference every other
    verification outcome uses.

    The copy names no reason. Stripe gives a last_error.code on some of
    these events and it is a machine code rather than an explanation, so
    repeating it at a member would assert something we cannot evidence. It
    is stored for operators instead.

    Fire-and-forget: notify() already swallows its own failures, and this
    catches anything it cannot, because a notification that blows up must
    never turn a webhook into a non-2xx and make Stripe retry.
    """
    try:
        from service.notifications import notify
        from emails.notification import verification_id_check_retry_email
        notify(
            person_id, "verification",
            title="Your ID check needs another try",
            body="You can start the check again when you are ready.",
            url="/verify/gold",
            email_subject="Your ID check needs another try",
            email_html_factory=verification_id_check_retry_email,
        )
    except Exception:
        import traceback
        print("identity_verification requires_input notify failed:")
        print(traceback.format_exc())


# ---------------------------------------------------------------------------
# POST /verification/start-id-flow
# ---------------------------------------------------------------------------

def post_start_id_flow(s):
    """Creates a Stripe Identity VerificationSession for `s.person_id` and
    returns its client_secret. The frontend opens that in expo-web-browser.

    Stripe links the session back to us via metadata.user_id, which the
    webhook then reads to promote the right user.
    """
    if not s or not s.person_id:
        return 'Not authorized', 401

    stripe = _stripe()
    if stripe is None:
        return 'Identity verification is not configured', 503

    try:
        session = stripe.identity.VerificationSession.create(
            type='document',
            metadata={'user_id': str(s.person_id)},
            options={
                'document': {
                    'require_matching_selfie': True,
                    'require_live_capture': True,
                    'require_id_number': False,
                },
            },
        )
    except Exception as e:
        logger.warning(f'Stripe Identity session create failed: {e}')
        return 'Could not start verification', 502

    return {
        'session_id':    session.id,
        'client_secret': session.client_secret,
        'url':           getattr(session, 'url', None),
    }


# ---------------------------------------------------------------------------
# POST /webhooks/stripe-identity
# ---------------------------------------------------------------------------

def _issuing_country(stripe, session: dict) -> Optional[str]:
    """The country that issued the ID document, from the VerificationReport.

    The old code read `verified_outputs.document.issuing_country` off the
    webhook's session object. Two things are wrong with that, and together
    they are why all 13 gold members in production carry a NULL country:

      * `verified_outputs` is "not returned by default; request it with the
        expand request parameter" (Stripe API reference, VerificationSession
        object), so a webhook payload does not carry it at all;
      * even when expanded it holds the person's verified data (address, dob,
        name, id_number). The DOCUMENT's `issuing_country` lives on the
        VerificationReport (Stripe API reference, VerificationReport object:
        `document.issuing_country`).

    So: take the report id off the session and fetch it. Falls back to the
    verified address country if a report is somehow unavailable, and returns
    None rather than raising, because a missing country must never cost a
    member the promotion they just earned.
    """
    report_id = session.get('last_verification_report')
    if isinstance(report_id, dict):          # already expanded
        document = report_id.get('document') or {}
        country = document.get('issuing_country')
        if country:
            return country
        report_id = report_id.get('id')
    if report_id and stripe is not None:
        try:
            report = stripe.identity.VerificationReport.retrieve(report_id)
            document = (report.get('document') if hasattr(report, 'get')
                        else getattr(report, 'document', None)) or {}
            country = (document.get('issuing_country') if hasattr(document, 'get')
                       else getattr(document, 'issuing_country', None))
            if country:
                return country
        except Exception as e:               # network, permissions, shape
            logger.warning('Could not read the verification report %s: %s', report_id, e)

    verified_outputs = session.get('verified_outputs') or {}
    address = verified_outputs.get('address') or {}
    return address.get('country') or None


def post_stripe_identity_webhook():
    """Stripe-signed webhook endpoint. No app auth — Stripe's signature is
    the auth. We look up the user via the session's metadata.user_id and
    promote them to 'gold' on `verification_session.verified`.

    The other session events are persisted against the same
    metadata.user_id (see record_identity_outcome) and only
    `requires_input` notifies, because it is the only one the member can
    act on. Anything else Stripe sends is acknowledged with a 200 and
    logged: a non-2xx makes Stripe retry the same event for days.
    """
    stripe = _stripe()
    if stripe is None or _webhook_secret is None:
        return 'Webhook not configured', 503

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    try:
        # Signature verification only — we discard the StripeObject
        # downstream because newer SDK versions trigger __getattr__('object')
        # on the discriminator field and raise AttributeError when test
        # payloads omit it. Plain json.loads gives predictable dict semantics.
        stripe.Webhook.construct_event(payload, sig_header, _webhook_secret)
    except ValueError:
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError:
        return 'Invalid signature', 400

    try:
        event_data = json.loads(
            payload.decode('utf-8') if isinstance(payload, bytes) else payload
        )
    except (ValueError, UnicodeDecodeError):
        return 'Invalid payload', 400

    event_type = event_data.get('type', '')
    obj = event_data.get('data', {}).get('object', {}) or {}

    if event_type == 'identity.verification_session.verified':
        meta = obj.get('metadata') or {}
        user_id = _coerce_int(meta.get('user_id'))
        if not user_id:
            logger.warning('Stripe verified event missing metadata.user_id')
            return {'received': True, 'promoted': False, 'reason': 'no_user_id'}

        country = _issuing_country(stripe, obj)
        if country is None:
            # Not fatal: the promotion is what the member is waiting on, and
            # the country is a record we keep about the document. Logged so a
            # run of empty countries is visible rather than silent, which is
            # how all 13 gold members in production ended up with no country
            # at all (found 2026-09-19).
            logger.warning('Stripe verified event: no issuing country resolved for user %s', user_id)

        promoted = promote_user(user_id, 'gold', country=country)
        return {'received': True, 'promoted': promoted}

    elif event_type in NON_VERIFIED_OUTCOMES:
        # These used to be acknowledged and dropped. `requires_input` is
        # Stripe's "the member has to try again", and dropping it left the
        # member on a screen that never resolved with nothing to act on.
        status = NON_VERIFIED_OUTCOMES[event_type]
        meta = obj.get('metadata') or {}
        user_id = _coerce_int(meta.get('user_id'))
        if not user_id:
            logger.warning('Stripe %s event missing metadata.user_id', status)
            return {'received': True, 'promoted': False, 'recorded': False,
                    'reason': 'no_user_id'}

        last_error = obj.get('last_error')
        error_code = (last_error.get('code')
                      if isinstance(last_error, dict) else None)

        recorded = record_identity_outcome(
            user_id, status,
            session_id=obj.get('id'),
            error_code=error_code,
            event_at=_event_time(event_data),
        )
        if not recorded:
            # No such member, or they are already gold and this latch
            # refuses to contradict a tier they earned, or this is a
            # redelivery of something already recorded, or it is older than
            # what is stored. `recorded` means "this is news", so the
            # notification below fires once per outcome however many times
            # Stripe sends it.
            logger.info(
                'Stripe %s event recorded nothing for user %s', status, user_id)
        elif status in NOTIFIED_OUTCOMES:
            _notify_id_check_needs_another_try(user_id)

        return {'received': True, 'promoted': False, 'recorded': recorded}

    # Unknown event types — return 200 so Stripe doesn't retry forever, but log.
    logger.info(f'Unhandled Stripe Identity event: {event_type}')
    return {'received': True, 'promoted': False}


def _event_time(event_data: dict) -> Optional[datetime]:
    """The event's own `created`, as a timestamp.

    Stripe sends this as seconds since the epoch on every event. It is the
    only ordering signal we get, because delivery order is not guaranteed
    and receipt time says nothing about which of two events happened first.
    Returns None when it is missing or unreadable, and the recorder then
    falls back to NOW() rather than refusing the write: a missing timestamp
    must never cost a member the record of what happened.
    """
    created = event_data.get('created')
    if created is None:
        return None
    try:
        return datetime.fromtimestamp(int(created), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _coerce_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
