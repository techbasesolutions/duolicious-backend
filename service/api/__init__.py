from pathlib import Path
from typing import Optional
from flask import request, Response, abort
import duotypes as t

# Task 0.5 telemetry: init Sentry as early as possible so import-time errors
# in the rest of this module are captured. No-op if SENTRY_DSN is unset.
from util.analytics import init_sentry as _init_sentry
_init_sentry()

from service import (
    checkout,
    decisions,
    discovery,
    identity_verification,
    location,
    person,
    revenuecat_webhook,
    search,
    translation,
)
# `question` import removed in Task 0.3c — Q&A subsystem strip.
# `discovery` added in Phase 1 Task 1.3 — country/language/long-distance prefs.
# `translation` added in Phase 2 Task 2.4 — outgoing translation preview.
# `identity_verification` added in Phase 3 Task 3.1 — Stripe Identity gold tier.
# `revenuecat_webhook` added in Phase 5 Task 5.2 — IAP receipt validation.
from database import api_tx
import psycopg
from service.api.decorators import (
    app,
    adelete,
    aget,
    apatch,
    apost,
    aput,
    delete,
    get,
    patch,
    post,
    put,
    validate,
    limiter,
    shared_otp_limit,
    disable_ip_rate_limit,
    disable_account_rate_limit,
    limiter_account,
)
import time
from antiabuse.antispam.signupemail import normalize_email
import json

_init_sql_file = (
    Path(__file__).parent.parent.parent / 'init-api.sql')

_migrations_sql_file = (
    Path(__file__).parent.parent.parent / 'migrations.sql')

_email_domains_bad_file = (
    Path(__file__).parent.parent.parent / 'email-domains-bad.sql')

_email_domains_good_file = (
    Path(__file__).parent.parent.parent / 'email-domains-good.sql')

_banned_club_file = (
    Path(__file__).parent.parent.parent / 'banned-club.sql')

def get_ttl_hash(seconds=10):
    """Return the same value withing `seconds` time period"""
    return round(time.time() / seconds)

def migrate_unnormalized_emails():
    """
    It'll probably be necessary to call this function again if/when
    `normalize_email` normalizes more address.
    """
    with api_tx() as tx:
        q = "SELECT 1 FROM person WHERE normalized_email ILIKE '%@googlemail.com' LIMIT 1"
        if tx.execute(q).fetchone():
            print('Unnormalized emails found. Normalizing...')
        else:
            print('Emails already normalized. Not performing normalization.')
            return

    with api_tx() as tx:
        print('Selecting emails')
        q = "SELECT email FROM person"
        tx.execute('SET LOCAL statement_timeout = 300000') # 5 minutes
        rows = tx.execute(q).fetchall()
        print('Done selecting emails')

    print('Computing normalized emails')
    params_seq = [
        row | dict(normalized_email=normalize_email(row['email']))
        for row in rows
    ]
    print('Done computing normalized emails')

    with api_tx('read committed') as tx:
        q = """
        UPDATE person SET
        normalized_email = %(normalized_email)s
        WHERE email = %(email)s
        """
        print('Updating normalized emails in `person` table')
        tx.execute('SET LOCAL statement_timeout = 300000') # 5 minutes
        tx.executemany(q, params_seq)
        print('Done updating normalized emails in `person` table')

        q = """
        UPDATE banned_person bp
        SET
            normalized_email = %(normalized_email)s
        WHERE
            normalized_email = %(email)s
        AND NOT EXISTS (
            SELECT
                1
            FROM
                banned_person
            WHERE
                normalized_email = %(normalized_email)s
            AND
                ip_address = bp.ip_address
        )
        """
        print('Updating normalized emails in `banned_person` table')
        tx.executemany(q, params_seq)
        print('Done updating normalized emails in `banned_person` table')

def maybe_run_init():
    with api_tx() as tx:
        row = tx.execute("SELECT to_regclass('person')").fetchone()

    if row ['to_regclass'] is not None:
        print('Database already initialized')
        return

    with open(_init_sql_file, 'r') as f:
        init_sql_file = f.read()

    with api_tx() as tx:
        tx.execute(init_sql_file)

def init_db():
    with open(_migrations_sql_file, 'r') as f:
        migrations_sql_file = f.read()

    with open(_email_domains_bad_file, 'r') as f:
        email_domains_bad_file = f.read()

    with open(_email_domains_good_file, 'r') as f:
        email_domains_good_file = f.read()

    with open(_banned_club_file, 'r') as f:
        banned_club_file = f.read()

    maybe_run_init()

    with api_tx() as tx:
        tx.execute('SET LOCAL statement_timeout = 300000') # 5 minutes
        tx.execute(migrations_sql_file)

    with api_tx() as tx:
        tx.execute(email_domains_bad_file)

    with api_tx() as tx:
        tx.execute(email_domains_good_file)

    with api_tx() as tx:
        tx.execute('SET LOCAL statement_timeout = 300000') # 5 minutes
        tx.execute(banned_club_file)

    migrate_unnormalized_emails()

@post('/request-otp', limiter=shared_otp_limit)
@validate(t.PostRequestOtp)
def post_request_otp(req: t.PostRequestOtp):
    limit = "40 per day"
    scope = "request_otp"

    with (
        limiter.limit(
            limit,
            scope=scope,
            exempt_when=disable_ip_rate_limit),
        limiter.limit(
            limit,
            scope=scope,
            key_func=limiter_account,
            exempt_when=disable_account_rate_limit)
    ):
        return person.post_request_otp(req)

@apost(
    '/resend-otp',
    limiter=shared_otp_limit,
    expected_onboarding_status=None,
    expected_sign_in_status=False
)
def post_resend_otp(s: t.SessionInfo):
    return person.post_resend_otp(s)

@apost(
    '/check-otp',
    expected_onboarding_status=None,
    expected_sign_in_status=False
)
@validate(t.PostCheckOtp)
def post_check_otp(req: t.PostCheckOtp, s: t.SessionInfo):
    limit = "40 per day"
    scope = "check_otp"

    with (
        limiter.limit(
            limit,
            scope=scope,
            exempt_when=disable_ip_rate_limit),
        limiter.limit(
            limit,
            scope=scope,
            key_func=limiter_account,
            exempt_when=disable_account_rate_limit)
    ):
        return person.post_check_otp(req, s)

@apost('/sign-out', expected_onboarding_status=None)
def post_sign_out(s: t.SessionInfo):
    return person.post_sign_out(s)

@apost('/check-session-token', expected_onboarding_status=None)
def post_check_session_token(s: t.SessionInfo):
    return person.post_check_session_token(s)

@aget(
    '/search-locations',
    expected_onboarding_status=None,
    expected_sign_in_status=None,
)
def get_search_locations(_):
    return location.get_search_locations(q=request.args.get('q'))

@apatch('/onboardee-info', expected_onboarding_status=False)
@validate(t.PatchOnboardeeInfo)
def patch_onboardee_info(req: t.PatchOnboardeeInfo, s: t.SessionInfo):
    return person.patch_onboardee_info(req, s)

@adelete('/onboardee-info', expected_onboarding_status=False)
@validate(t.DeleteOnboardeeInfo)
def delete_onboardee_info(req: t.DeleteOnboardeeInfo, s: t.SessionInfo):
    return person.delete_onboardee_info(req, s)

@apost('/finish-onboarding', expected_onboarding_status=False)
def post_finish_onboarding(s: t.SessionInfo):
    return person.post_finish_onboarding(s)

# /next-questions, POST /answer, DELETE /answer routes removed in Task 0.3c —
# Q&A subsystem strip per audit. The `person.post_answer` / `person.delete_answer`
# methods will be removed in Task 0.3f.

@aget('/search')
def get_search(s: t.SessionInfo):
    n = request.args.get('n')
    o = request.args.get('o')

    rawClub = request.args.get('club')
    lowerClub = None if rawClub is None else rawClub.lower().strip()

    club = (
        search.ClubHttpArg(lowerClub if lowerClub != '\0' else None)
        if 'club' in request.args
        else None
    )

    search_type, _ = search.get_search_type(n, o)

    limit = "15 per 2 minutes"
    scope = json.dumps([search_type, lowerClub])

    if search_type == 'uncached-search':
        with (
            limiter.limit(
                limit,
                scope=scope,
                exempt_when=disable_ip_rate_limit),
            limiter.limit(
                limit,
                scope=scope,
                key_func=limiter_account,
                exempt_when=disable_account_rate_limit)
        ):
            return search.get_search(s=s, n=n, o=o, club=club)
    else:
        return search.get_search(s=s, n=n, o=o, club=club)

@get('/health', limiter=limiter.exempt)
def get_health():
    return 'status: ok'

# Image proxy — streams photos from the private S3-compatible bucket.
# Filename pattern matches the keys put_object writes:
#   `${size}-${uuid}.jpg` for the rendered variants (450 / 900 / original)
# Auth-free by design: photo UUIDs are unguessable v4s and the typical
# CDN pattern is unauth. This is the same pattern as the upstream
# Duolicious frontend uses against its CDN bucket. We re-use the
# `bucket` resource configured at module import time in service/person.
@get('/image/<filename>', limiter=limiter.exempt)
def get_image(filename: str):
    # Defensive: only allow safe key patterns (no path traversal, no
    # arbitrary keys). Filenames look like "450-<uuid>.jpg" / "900-<uuid>.jpg"
    # / "original-<uuid>.jpg" / "<uuid>.gif" — alnum + dashes + dots only.
    if '/' in filename or '..' in filename or len(filename) > 128:
        abort(400)
    try:
        obj = person.bucket.Object(filename).get()
    except Exception:
        abort(404)
    body = obj['Body'].read()
    content_type = obj.get('ContentType') or 'image/jpeg'
    # Cache aggressively — UUIDs make these immutable. 1 year + immutable.
    return Response(
        body,
        status=200,
        headers={
            'Content-Type': content_type,
            'Cache-Control': 'public, max-age=31536000, immutable',
        },
    )

@aget('/me')
def get_me_by_session(s: t.SessionInfo):
    return person.get_me(person_id_as_int=s.person_id)

@get('/me/<person_id>')
def get_me_by_id(person_id: str):
    return person.get_me(person_id_as_str=person_id)

@aget('/prospect-profile/<prospect_uuid>', auth='optional')
def get_prospect_profile(
    s: Optional[t.SessionInfo],
    prospect_uuid: str,
):
    return person.get_prospect_profile(s, prospect_uuid)

@aget('/conversation-prospect/<prospect_uuid>')
def get_conversation_prospect(s: t.SessionInfo, prospect_uuid: str):
    return person.get_conversation_prospect(s, prospect_uuid)

@aget('/blocked')
def get_blocked(s: t.SessionInfo):
    """List the people the current user has blocked (skipped + reported).
    Frontend renders this on /settings/blocked. Each row has uuid, name,
    and a `blocked_at` ISO timestamp the UI formats as 'X days ago'."""
    with api_tx('READ COMMITTED') as tx:
        rows = tx.execute(
            """
            SELECT
                p.uuid::text AS uuid,
                p.name AS name,
                sk.created_at AS blocked_at
            FROM skipped sk
            JOIN person p ON p.id = sk.object_person_id
            WHERE sk.subject_person_id = %(person_id)s
              AND sk.reported = TRUE
            ORDER BY sk.created_at DESC
            """,
            dict(person_id=s.person_id),
        ).fetchall()
    return [
        {
            'uuid': r['uuid'],
            'name': r['name'],
            'blocked_at': r['blocked_at'].isoformat() if r.get('blocked_at') else None,
        }
        for r in rows
    ]

@apost('/skip/by-uuid/<prospect_uuid>')
@validate(t.PostSkip)
def post_skip_by_uuid(req: t.PostSkip, s: t.SessionInfo, prospect_uuid: str):
    limit = "1 per 5 seconds; 20 per day"
    scope = "report"

    if req.report_reason:
        with (
            limiter.limit(
                limit,
                scope=scope,
                exempt_when=disable_ip_rate_limit),
            limiter.limit(
                limit,
                scope=scope,
                key_func=limiter_account,
                exempt_when=disable_account_rate_limit)
        ):
            return person.post_skip_by_uuid(req, s, prospect_uuid)
    else:
        return person.post_skip_by_uuid(req, s, prospect_uuid)

# TODO: Delete
@apost('/unskip/<int:prospect_person_id>')
def post_unskip(s: t.SessionInfo, prospect_person_id: int):
    return person.post_unskip(s, prospect_person_id)

@apost('/unskip/by-uuid/<prospect_uuid>')
def post_unskip_by_uuid(s: t.SessionInfo, prospect_uuid: str):
    return person.post_unskip_by_uuid(s, prospect_uuid)

# /compare-personalities and /compare-answers/<id> routes removed in Task 0.3c.
# The `person.get_compare_personalities` / `person.get_compare_answers` methods
# will be removed in Task 0.3f.

# Phase W match loop — record likes / list matches / fetch one match.
# See service/decisions/__init__.py + migrations/0006_match_loop.sql.
@apost('/decisions/reset')
def post_decisions_reset(s: t.SessionInfo):
    """Wipe the current user's swipe history (liked + skipped + matches).
    Phase W cutover testing convenience — lets a developer / seed
    account exhaust the candidate pool, then reset to re-test the
    full /discover loop without manual DB intervention.

    Test-mode semantics: this is destructive. We delete:
      - rows where this user is the SUBJECT in skipped/liked/swipe,
        AND rows where this user is the OBJECT (so a peer's prior
        block of this user also clears — both halves needed for
        Q_UNCACHED_SEARCH_2's bidirectional skipped exclusion)
      - ahavah_match rows where this user is either user_a or user_b
        (matches re-form on the next mutual like; without this delete
        Ehud + Jada would stay in /matches and never reappear in
        each other's /discover during a re-test)
      - the user's search_cache so /search recomputes
    """
    with api_tx() as tx:
        # Bidirectional skipped wipe — clears both Ehud→Jada and
        # Jada→Ehud rows when Ehud resets. Necessary for symmetric
        # re-test (any one-sided block would otherwise persist).
        tx.execute(
            """
            DELETE FROM skipped
             WHERE subject_person_id = %(p)s
                OR object_person_id  = %(p)s
            """,
            dict(p=s.person_id),
        )
        # Bidirectional liked wipe — same reason; reset wipes incoming
        # likes too so the peer can re-like from a clean slate.
        tx.execute(
            """
            DELETE FROM liked
             WHERE liker_id = %(p)s
                OR liked_id = %(p)s
            """,
            dict(p=s.person_id),
        )
        # `swipe` is the upstream Duolicious swipe-history table that
        # Q_UNCACHED_SEARCH_2 also excludes against. Without this
        # delete, /discover stayed empty after a reset because every
        # prospect was still marked as "already swiped".
        tx.execute(
            "DELETE FROM swipe WHERE swiper_person_id = %(p)s",
            dict(p=s.person_id),
        )
        # ahavah_match — Phase W match-loop table. Match rows survive
        # liked/skipped wipes by FK (CASCADE only fires on person row
        # deletion). Explicit delete here so a reset truly returns the
        # pair to the "never met" state for re-test. Cascade also
        # removes any chat history tied to the match (mam_message,
        # inbox conversation rows) per the FK chain in init-api.sql.
        tx.execute(
            """
            DELETE FROM ahavah_match
             WHERE user_a_id = %(p)s
                OR user_b_id = %(p)s
            """,
            dict(p=s.person_id),
        )
        # Clear the search_cache so the next /search recomputes
        # against the now-clean swipe/skipped/match state.
        tx.execute(
            "DELETE FROM search_cache WHERE searcher_person_id = %(p)s",
            dict(p=s.person_id),
        )
    return {'ok': True}

@apost('/decisions')
@validate(t.PostDecision)
def post_decisions(req: t.PostDecision, s: t.SessionInfo):
    return decisions.post_decisions(req, s)

@aget('/matches')
def get_matches(s: t.SessionInfo):
    return decisions.get_matches(s)

@aget('/matches/<match_id>')
def get_match(s: t.SessionInfo, match_id: str):
    return decisions.get_match(s, match_id)

@aget('/likes/incoming')
def get_incoming_likes(s: t.SessionInfo):
    """People who've liked the session user but the user hasn't decided
    on yet. Powers the /matches 'Liked you' tab on the frontend."""
    return decisions.get_incoming_likes(s)

@apost('/inbox-info')
@validate(t.PostInboxInfo)
def post_inbox_info(req: t.PostInboxInfo, s: t.SessionInfo):
    return person.post_inbox_info(req, s)

@apost('/account/change-email-request')
@validate(t.PostChangeEmailRequest)
def post_change_email_request(req: t.PostChangeEmailRequest, s: t.SessionInfo):
    """Stage an email change. Sends an OTP to the new address; the
    user submits it via /account/change-email-verify to complete the swap."""
    return person.change_email_request(s, req.new_email)

@apost('/account/change-email-verify')
@validate(t.PostChangeEmailVerify)
def post_change_email_verify(req: t.PostChangeEmailVerify, s: t.SessionInfo):
    """Verify the OTP sent by /account/change-email-request and swap
    the email on the person row."""
    return person.change_email_verify(s, req.otp)

@adelete('/account')
def delete_account(s: t.SessionInfo):
    # Task 0.7 note: this is the upstream Duolicious fork's existing immediate hard-delete.
    # Plan Phase 5 enhancement: convert to soft-delete + 7-day grace +
    # cancel-link email (Resend). The user's deletion_requested_at gets set;
    # the cron worker hard-deletes after grace expires. Required for the
    # "wait, I clicked delete by mistake" recovery path App Store reviewers
    # tend to test.
    return person.delete_or_ban_account(s=s)


@apost('/account/cancel-deletion')
def post_account_cancel_deletion(s: t.SessionInfo):
    """Phase W cutover (2026-05-15): self-service undo of a pending
    soft-delete. Restores activated=TRUE + clears
    deletion_requested_at, so the pendingdeletion cron stops
    considering the row + the user reappears in /search + /matches.
    Idempotent — no-op on already-active accounts."""
    return person.cancel_account_deletion(s=s)


@aget('/billing-portal')
def get_billing_portal(s: t.SessionInfo):
    """Phase W cutover (2026-05-15): Stripe Customer Portal session
    for self-service subscription management. Requires the user to
    have completed at least one paid Checkout (which stamps
    person.stripe_customer_id via the webhook); free users get 400."""
    return checkout.get_billing_portal(s=s)

@aget('/account/export')
def get_account_export(s: t.SessionInfo):
    """GDPR right-to-portability export. Returns the user's profile data as
    JSON. Phase 0 Task 0.7 stub — currently returns the same shape as
    `/profile-info`. Phase 5+ extends to include messages history, swipes,
    and matches in a portable archive."""
    return person.get_profile_info(s)

# --- Phase 1 Task 1.3 — discovery preferences -----------------------------

@aget('/discovery-prefs')
def get_discovery_prefs(s: t.SessionInfo):
    return discovery.get_discovery_prefs(s)

@apatch('/discovery-prefs')
def patch_discovery_prefs(s: t.SessionInfo):
    return discovery.patch_discovery_prefs(s)

@apost('/search-preference-country')
def post_search_preference_country(s: t.SessionInfo):
    return discovery.post_search_preference_country(s)

@apost('/search-preference-language')
def post_search_preference_language(s: t.SessionInfo):
    return discovery.post_search_preference_language(s)

@apost('/search-preference-long-distance')
def post_search_preference_long_distance(s: t.SessionInfo):
    return discovery.post_search_preference_long_distance(s)

@apost('/translate-preview')
def post_translate_preview(s: t.SessionInfo):
    return translation.post_translate_preview(s)

@apost('/verification/start-id-flow')
def post_start_id_flow(s: t.SessionInfo):
    return identity_verification.post_start_id_flow(s)

@apost('/checkout/web')
@validate(t.PostCheckoutWeb)
def post_checkout_web(req: t.PostCheckoutWeb, s: t.SessionInfo):
    """Create a Stripe Checkout session for the requested premium tier
    and return the hosted URL the frontend redirects to. Phase W cutover."""
    return checkout.post_checkout_web(s, req)

@post('/webhooks/stripe-identity')
def post_stripe_identity_webhook():
    return identity_verification.post_stripe_identity_webhook()

@post('/webhooks/stripe-checkout')
def post_stripe_checkout_webhook():
    return checkout.post_stripe_checkout_webhook()

@post('/webhooks/revenuecat')
def post_revenuecat_webhook():
    return revenuecat_webhook.post_revenuecat_webhook()

@apost('/deactivate')
def post_deactivate(s: t.SessionInfo):
    return person.post_deactivate(s=s)

@aget('/profile-info')
def get_profile_info(s: t.SessionInfo):
    return person.get_profile_info(s)

@adelete('/profile-info')
@validate(t.DeleteProfileInfo)
def delete_profile_info(req: t.DeleteProfileInfo, s: t.SessionInfo):
    return person.delete_profile_info(req, s)

@apatch('/profile-info')
@validate(t.PatchProfileInfo)
def patch_profile_info(req: t.PatchProfileInfo, s: t.SessionInfo):
    return person.patch_profile_info(req, s)

@aget('/search-filters')
def get_search_filers(s: t.SessionInfo):
    return person.get_search_filters(s)

@apost('/search-filter')
@validate(t.PostSearchFilter)
def post_search_filter(req: t.PostSearchFilter, s: t.SessionInfo):
    return person.post_search_filter(req, s)

# /search-filter-questions and /search-filter-answer routes removed in Task 0.3c.
# The `person.post_search_filter_answer` method will be removed in Task 0.3f.

@aget('/search-clubs')
def get_search_clubs(s: t.SessionInfo):
    return person.get_search_clubs(s=s, search_str=request.args.get('q', ''))

@get('/search-public-clubs')
def get_search_public_clubs():
    return person.get_search_clubs(
            s=None, search_str=request.args.get('q', ''), allow_empty=True)

@apost('/join-club')
@validate(t.PostJoinClub)
def post_join_club(req: t.PostJoinClub, s: t.SessionInfo):
    return person.post_join_club(req, s)

@apost('/leave-club')
@validate(t.PostLeaveClub)
def post_leave_club(req: t.PostLeaveClub, s: t.SessionInfo):
    return person.post_leave_club(req, s)

@get('/update-notifications')
def get_update_notifications():
    return person.get_update_notifications(
        email=request.args.get('email', ''),
        type=request.args.get('type', ''),
        frequency=request.args.get('frequency', ''),
    )

@aget('/feed')
def get_feed(s: t.SessionInfo):
    valid_datetime = t.ValidDatetime.model_validate(
        {'datetime': request.args.get('before')}
    )

    return search.get_feed(s=s, before=valid_datetime.datetime)

@apost('/verification-selfie')
@validate(t.PostVerificationSelfie)
def post_verification_selfie(req: t.PostVerificationSelfie, s: t.SessionInfo):
    return person.post_verification_selfie(req, s)

@apost('/verify')
def post_verify(s: t.SessionInfo):
    limit = "8 per day"
    scope = "verify"

    with (
        limiter.limit(
            limit,
            scope=scope,
            exempt_when=disable_ip_rate_limit),
        limiter.limit(
            limit,
            scope=scope,
            key_func=limiter_account,
            exempt_when=disable_account_rate_limit)
    ):
        return person.post_verify(s)

@aget('/check-verification')
def get_check_verification(s: t.SessionInfo):
    return person.get_check_verification(s=s)

@apost('/dismiss-donation')
def post_dismiss_donation(s: t.SessionInfo):
    return person.post_dismiss_donation(s=s)

@get('/stats')
def get_stats():
    return person.get_stats(
        ttl_hash=get_ttl_hash(seconds=60),
        club_name=request.args.get('club-name'))

@get('/gender-stats')
def get_gender_stats():
    return person.get_gender_stats(ttl_hash=get_ttl_hash(seconds=60))

@get('/admin/ban-link/<token>')
def get_admin_ban_link(token: str):
    return person.get_admin_ban_link(token)

@get('/admin/ban/<token>')
def get_admin_ban(token: str):
    return person.get_admin_ban(token)

@get('/admin/delete-photo-link/<token>')
def get_admin_delete_photo_link(token: str):
    return person.get_admin_delete_photo_link(token)

@get('/admin/delete-photo/<token>')
def get_admin_delete_photo(token: str):
    return person.get_admin_delete_photo(token)

@aget('/export-data-token')
def get_export_data_token(s: t.SessionInfo):
    limit = "3 per day"
    scope = "export_data_token"

    with (
        limiter.limit(
            limit,
            scope=scope,
            exempt_when=disable_ip_rate_limit),
        limiter.limit(
            limit,
            scope=scope,
            key_func=limiter_account,
            exempt_when=disable_account_rate_limit)
    ):
        return person.get_export_data_token(s=s)

@get('/export-data/<token>')
def get_export_data(token: str):
    return person.get_export_data(token=token)

@post('/revenuecat')
@validate(t.PostRevenuecat)
def post_revenuecat(req: t.PostRevenuecat):
    return person.post_revenuecat(req)

@aget('/visitors')
def get_visitors(s: t.SessionInfo):
    return person.get_visitors(s=s)

@apost('/mark-visitors-checked')
@validate(t.PostMarkVisitorsChecked)
def post_mark_visitors_checked(req: t.PostMarkVisitorsChecked, s: t.SessionInfo):
    return person.post_mark_visitors_checked(req=req, s=s)


# Phase W push notifications - registered via a sibling module so we
# don't touch the brittle top-level `from service import (...)` block.
# See docstring at the top of notifications_routes.py for the why.
import service.api.notifications_routes  # noqa: E402,F401
# Same sibling-routes pattern for /admin/reports — gated on
# person.roles && ARRAY['admin','mod'] inside the handler.
import service.api.moderation_routes  # noqa: E402,F401
