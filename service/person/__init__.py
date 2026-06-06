import os
from html import escape as html_escape
from database import api_tx, fetchall_sets
from typing import Any, Optional, Iterable, Tuple, Literal
import duotypes as t
import json
import secrets
from duohash import sha512
from PIL import Image
import io
import boto3
from concurrent.futures import ThreadPoolExecutor, as_completed
from service.config import API_BASE_URL, EMAIL_DOMAIN, PRODUCT_NAME, SIGNUPS_OPEN, SIGNUP_ALLOWED_DOMAINS
from service.person.sql import *
from service.search.sql import *
from commonsql import *
from service.person.template import otp_template
from service.referrals import (
    attribute as attribute_referral,
    credit_pending_for_invitee,
    credit_pending_for_inviter,
)
import traceback
import re
from smtp import aws_smtp
from flask import request, send_file
from dataclasses import dataclass
import psycopg
from functools import lru_cache
from antiabuse.antispam.signupemail import (
    check_and_update_bad_domains,
    normalize_email,
)
from antiabuse.lodgereport import (
    skip_by_uuid,
)
from antiabuse.firehol import firehol as _firehol_impl

# Phase W staging: the FireHOL multiprocessing-based block-list helper
# has a child-process fragility that intermittently kills /request-otp
# and /check-otp under light staging load (the `_rpc` call gets EOFError
# when the child dies, surfacing as 500s). Set `DUO_DISABLE_FIREHOL=true`
# to skip the IP-blocklist check entirely — safe for staff-only staging,
# NEVER for production where it's actual anti-abuse defence.
import os as _os
if _os.environ.get("DUO_DISABLE_FIREHOL", "false").lower() in ("true", "1", "yes"):
    # Loud-warn at import time so the bypass can't silently drift unnoticed
    # (audit Auth #12). Currently intentionally enabled on the droplet
    # because firehol's child process was OOM-killed under load on the
    # 4GB tier; revisit when the droplet is resized or firehol is replaced.
    print(
        "WARNING: DUO_DISABLE_FIREHOL=true — IP blocklist is OFF. "
        "/request-otp + /check-otp lose their IP-reputation layer. "
        "Set DUO_DISABLE_FIREHOL=false (or unset) when the constraint "
        "that forced the bypass is gone."
    )
    class _FireholBypass:
        def matches(self, _ip):
            return False
    firehol = _FireholBypass()
else:
    firehol = _firehol_impl
import blurhash
import numpy
import erlastic
from datetime import datetime, timezone
from duoaudio import put_audio_in_object_store
from service.person.aboutdiff import diff_addition_with_context
from verification.messages import (
    V_QUEUED,
    V_REUSED_SELFIE,
    V_UPLOADING_PHOTO,
)


class BytesEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            try:
                return obj.decode('utf-8')
            except:
                return str(obj)

        return super().default(obj)

DUO_ENV = os.environ['DUO_ENV']

R2_ACCT_ID = os.environ['DUO_R2_ACCT_ID']
R2_ACCESS_KEY_ID = os.environ['DUO_R2_ACCESS_KEY_ID']
R2_ACCESS_KEY_SECRET = os.environ['DUO_R2_ACCESS_KEY_SECRET']
R2_BUCKET_NAME = os.environ['DUO_R2_BUCKET_NAME']

BOTO_ENDPOINT_URL = os.getenv(
    'DUO_BOTO_ENDPOINT_URL',
    f'https://{R2_ACCT_ID}.r2.cloudflarestorage.com'
)

s3 = boto3.resource(
    's3',
    endpoint_url=BOTO_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_ACCESS_KEY_SECRET,
)

bucket = s3.Bucket(R2_BUCKET_NAME)

def init_db():
    pass

@dataclass
class CropSize:
    top: int
    left: int

def process_image_as_image(
    image: Image.Image,
    output_size: Optional[int] = None,
    crop_size: Optional[CropSize] = None,
) -> io.BytesIO:
    # Rotate the image according to EXIF data
    try:
        exif = image.getexif()
        orientation = exif[274] # 274 is the exif code for the orientation tag
    except:
        orientation = None

    if orientation is None:
        pass
    elif orientation == 1:
        # Normal, no changes needed
        pass
    elif orientation == 2:
        # Mirrored horizontally
        pass
    elif orientation == 3:
        # Rotated 180 degrees
        image = image.rotate(180, expand=True)
    elif orientation == 4:
        # Mirrored vertically
        pass
    elif orientation == 5:
        # Transposed
        image = image.rotate(-90, expand=True)
    elif orientation == 6:
        # Rotated -90 degrees
        image = image.rotate(-90, expand=True)
    elif orientation == 7:
        # Transverse
        image = image.rotate(90, expand=True)
    elif orientation == 8:
        # Rotated 90 degrees
        image = image.rotate(90, expand=True)

    # Crop the image to be square
    if output_size is not None:
        # Get the dimensions of the image
        width, height = image.size

        # Find the smaller dimension
        min_dim = min(width, height)

        # Compute the area to crop
        if crop_size is None:
            left = (width - min_dim) // 2
            top = (height - min_dim) // 2
            right = (width + min_dim) // 2
            bottom = (height + min_dim) // 2
        else:
            # Ensure the top left point is within range
            crop_size.top  = max(0, crop_size.top)
            crop_size.left = max(0, crop_size.left)

            crop_size.top  = min(height - min_dim, crop_size.top)
            crop_size.left = min(width  - min_dim, crop_size.left)

            # Compute the area to crop
            left = crop_size.left
            top = crop_size.top
            right = crop_size.left + min_dim
            bottom = crop_size.top + min_dim

        # Crop the image to be square
        crop_box = (left, top, right, bottom)
        image = image.crop(crop_box)

    # Scale the image to the desired size
    if output_size is not None and output_size != min_dim:
        image = image.resize((output_size, output_size))

    return image.convert('RGB')

def process_image_as_bytes(
    base64_file: t.Base64File,
    format: Literal['raw', 'jpeg'],
    output_size: Optional[int] = None,
    crop_size: Optional[CropSize] = None,
) -> io.BytesIO:
    if format == 'raw':
        return io.BytesIO(base64_file.bytes)

    output_bytes = io.BytesIO()

    image = process_image_as_image(base64_file.image, output_size, crop_size)

    image.save(
        output_bytes,
        format=format,
        quality=85,
        subsampling=2,
        progressive=True,
        optimize=True,
    )

    output_bytes.seek(0)

    return output_bytes

def compute_blurhash(image: Image.Image, crop_size: Optional[CropSize] = None):
    image = process_image_as_image(image, output_size=32, crop_size=crop_size)

    return blurhash.encode(numpy.array(image.convert("RGB")))

def put_image_in_object_store(
    uuid: str,
    base64_file: t.Base64File,
    crop_size: CropSize,
    sizes: list[Literal[None, 900, 450]] = [None, 900, 450],
):
    key_img = [
        (
            f'{size if size else "original"}-{uuid}.jpg',
            process_image_as_bytes(
                base64_file=base64_file,
                format='jpeg',
                output_size=size,
                crop_size=None if size is None else crop_size
            )
        )
        for size in sizes
    ]

    if base64_file.image.format == 'GIF' and None in sizes:
        key_img.append((
            f'{uuid}.gif',
            process_image_as_bytes(base64_file=base64_file, format='raw')
        ))

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(bucket.put_object, Key=key, Body=img)
            for key, img in key_img}

        for future in as_completed(futures):
            future.result()

def _has_gold(person_id: int) -> bool:
    with api_tx() as tx:
        tx.execute(Q_HAS_GOLD, dict(person_id=person_id))
        row = tx.fetchone()
    return row.get('has_gold', False)



def _send_otp(email: str, otp: str):
    if email.endswith('@example.com'):
        return

    aws_smtp.send(
        subject=f"Sign in to {PRODUCT_NAME}",
        body=otp_template(otp),
        to_addr=email,
        from_addr=f'noreply-otp@{EMAIL_DOMAIN}',
        # Route confused-user replies to a human address instead of the
        # noreply alias (which has no inbound MX) — audit Email #9.
        reply_to=f'hello@{EMAIL_DOMAIN}',
    )

def post_request_otp(req: t.PostRequestOtp):
    # Honeypot: bots that scrape the form and submit every field hit this.
    # Real users never see the field. Return a success-shaped response so
    # the bot can't tell it was rejected.
    from service.antibot import is_honeypot_hit, verify_turnstile
    if is_honeypot_hit(req.website):
        return dict(session_token=secrets.token_hex(64))

    # Pre-launch gate FIRST: signups closed to the public until launch.
    # Run before firehol + disposable so closed-beta callers don't probe
    # those side-effecting tables and so the response shape is identical
    # for every disallowed (banned/disposable/disallowed-domain) case
    # behind a single 403 (audit Auth #3).
    norm = normalize_email(req.email)
    if not SIGNUPS_OPEN and norm.rpartition("@")[2] not in SIGNUP_ALLOWED_DOMAINS:
        return 'Signups are not open yet', 403

    # Turnstile gate (no-op when TURNSTILE_SECRET_KEY unset — zero-config
    # rollout). Closed-beta allow-listed callers go through above; only
    # public callers reach here, so the verify is gated on launch.
    if not verify_turnstile(req.turnstile_token, request.remote_addr):
        return 'Verification failed', 403

    if not request.remote_addr or firehol.matches(request.remote_addr):
        return 'IP address blocked', 460

    if not check_and_update_bad_domains(req.email):
        return 'Disposable email', 400

    session_token = secrets.token_hex(64)
    session_token_hash = sha512(session_token)

    params = dict(
        email=req.email,
        normalized_email=normalize_email(req.email),
        pending_club_name=req.pending_club_name,
        is_dev=DUO_ENV == 'dev',
        session_token_hash=session_token_hash,
        ip_address=request.remote_addr,
    )

    with api_tx() as tx:
        # Purge any stale UNSIGNED-IN sessions for this email first so the
        # zoo of pre-auth bearers doesn't grow unbounded across attempts
        # (audit Auth #5). Signed-in sessions on other devices stay.
        # Matches on duo_session.email (the literal stored value); future
        # cleanup work could also normalize case + dot/plus aliases SQL-side.
        tx.execute(
            Q_PURGE_STALE_UNSIGNED_SESSIONS,
            dict(email=req.email),
        )
        rows = tx.execute(Q_INSERT_DUO_SESSION, params).fetchall()

        # Record referral attribution if the FE carried an inviter_code
        # from /i/<code>. Best-effort — bad codes / self-referrals /
        # already-attributed invitees return None silently. Same tx so
        # any later failure rolls this back too. See parent spec.
        attribute_referral(tx, getattr(req, "inviter_code", None), req.email)

    try:
        row, *_ = rows
        otp = row['otp']
    except:
        return 'Banned', 461

    _send_otp(req.email, otp)

    return dict(session_token=session_token)

def post_resend_otp(s: t.SessionInfo):
    if not request.remote_addr or firehol.matches(request.remote_addr):
        return 'IP address blocked', 460

    params = dict(
        email=s.email,
        normalized_email=normalize_email(s.email),
        is_dev=DUO_ENV == 'dev',
        session_token_hash=s.session_token_hash,
        ip_address=request.remote_addr,
    )

    with api_tx() as tx:
        rows = tx.execute(Q_UPDATE_OTP, params).fetchall()

    try:
        row, *_ = rows
        otp = row['otp']
    except:
        return 'Banned', 461

    _send_otp(s.email, otp)

def post_check_otp(req: t.PostCheckOtp, s: t.SessionInfo):
    if not request.remote_addr or firehol.matches(request.remote_addr):
        return 'IP address blocked', 460

    params = dict(
        otp=req.otp,
        session_token_hash=s.session_token_hash,
        pending_club_name=s.pending_club_name,
    )

    with api_tx() as tx:
        tx.execute(Q_MAYBE_DELETE_ONBOARDEE, params)
        tx.execute(Q_MAYBE_SIGN_IN, params)
        row = tx.fetchone()

        if not row:
            # Wrong OTP. Increment the per-session attempt counter and
            # null the OTP after 5 failures so it cannot be brute-forced
            # within its 10-minute lifetime (audit Auth #1).
            attempts_row = tx.execute(
                Q_INCREMENT_OTP_ATTEMPTS,
                dict(session_token_hash=s.session_token_hash),
            ).fetchone()
            if attempts_row and attempts_row.get('locked'):
                return 'Too many attempts. Request a new code.', 401
            return 'Invalid OTP', 401

        club_params = dict(
            person_id=s.person_id,
            club_name=s.pending_club_name,
            pending_club_name=s.pending_club_name,
            do_modify=True,
        )

        if \
                club_params['person_id'] is not None and \
                club_params['club_name'] is not None:
            tx.execute(Q_JOIN_CLUB, club_params)
            tx.execute(Q_UPSERT_SEARCH_PREFERENCE_CLUB, club_params)

        clubs = tx.execute(Q_GET_SESSION_CLUBS, club_params).fetchone()

        tx.execute(Q_UPDATE_LAST, dict(person_uuid=row['person_uuid']))

    return dict(
        onboarded=row['person_id'] is not None,
        **row,
        **clubs,
    )

def post_sign_out(s: t.SessionInfo):
    params = dict(session_token_hash=s.session_token_hash)

    with api_tx('READ COMMITTED') as tx:
        tx.execute(Q_DELETE_DUO_SESSION, params)


def post_sign_out_everywhere(s: t.SessionInfo):
    """Wipe EVERY duo_session row for this person — including the caller's
    own. Used when a user wants to revoke a stolen token they no longer
    control (audit Auth #8). Returns the count for the client to display."""
    with api_tx('READ COMMITTED') as tx:
        row = tx.execute(
            Q_DELETE_DUO_SESSIONS_FOR_PERSON,
            dict(person_id=s.person_id),
        ).fetchone()
    return {'revoked': (row or {}).get('n', 0)}

def post_check_session_token(s: t.SessionInfo):
    params = dict(
        person_id=s.person_id,
        pending_club_name=s.pending_club_name,
    )

    with api_tx() as tx:
        row = tx.execute(Q_CHECK_SESSION_TOKEN, params).fetchone()

        if not row:
            return 'Invalid token', 401

        club_params = dict(
            person_id=s.person_id,
            pending_club_name=s.pending_club_name,
        )

        clubs = tx.execute(Q_GET_SESSION_CLUBS, club_params).fetchone()

        return dict(
            person_id=s.person_id,
            person_uuid=s.person_uuid,
            onboarded=s.onboarded,
            **row,
            **clubs,
        )

def patch_onboardee_info(req: t.PatchOnboardeeInfo, s: t.SessionInfo):
    [field_name] = req.__pydantic_fields_set__
    field_value = req.dict()[field_name]

    if field_name in ['name', 'date_of_birth']:
        params = dict(
            email=s.email,
            field_value=field_value
        )

        q_set_onboardee_field = """
            INSERT INTO onboardee (
                email,
                $field_name
            ) VALUES (
                %(email)s,
                %(field_value)s
            ) ON CONFLICT (email) DO UPDATE SET
                $field_name = EXCLUDED.$field_name
            """.replace('$field_name', field_name)

        with api_tx() as tx:
            tx.execute(q_set_onboardee_field, params)
    elif field_name == 'location':
        params = dict(
            email=s.email,
            long_friendly=field_value
        )

        q_set_onboardee_field = """
            INSERT INTO onboardee (
                email,
                coordinates
            ) SELECT
                %(email)s,
                coordinates
            FROM location
            WHERE long_friendly = %(long_friendly)s
            ON CONFLICT (email) DO UPDATE SET
                coordinates = EXCLUDED.coordinates
            """
        with api_tx() as tx:
            tx.execute(q_set_onboardee_field, params)
            if tx.rowcount != 1:
                return 'Unknown location', 400
    elif field_name == 'gender':
        params = dict(
            email=s.email,
            gender=field_value
        )

        q_set_onboardee_field = """
            INSERT INTO onboardee (
                email,
                gender_id
            ) SELECT
                %(email)s,
                id
            FROM gender
            WHERE name = %(gender)s
            ON CONFLICT (email) DO UPDATE SET
                gender_id = EXCLUDED.gender_id
            """

        with api_tx() as tx:
            tx.execute(q_set_onboardee_field, params)
    elif field_name == 'other_peoples_genders':
        params = dict(
            email=s.email,
            genders=field_value
        )

        q_set_onboardee_field = """
            INSERT INTO onboardee_search_preference_gender (
                email,
                gender_id
            )
            SELECT
                %(email)s,
                id
            FROM gender
            WHERE name = ANY(%(genders)s)
            ON CONFLICT (email, gender_id) DO UPDATE SET
                gender_id = EXCLUDED.gender_id
            """

        with api_tx() as tx:
            tx.execute(q_set_onboardee_field, params)
    elif field_name == 'ahavah_extra':
        # Merge partial JSONB patch into onboardee.ahavah_extra. The
        # wizard fires one PATCH per Ahavah-specific field (assembly,
        # torahLevel, etc.) so the merge accumulates the user's
        # answers; /finish-onboarding then copies the resulting blob
        # onto the new person row.
        params = dict(
            email=s.email,
            field_value=json.dumps(field_value),
        )
        q_set_onboardee_field = """
            INSERT INTO onboardee (email, ahavah_extra)
            VALUES (%(email)s, %(field_value)s::jsonb)
            ON CONFLICT (email) DO UPDATE SET
                ahavah_extra = onboardee.ahavah_extra || EXCLUDED.ahavah_extra
            """
        with api_tx() as tx:
            tx.execute(q_set_onboardee_field, params)
    elif field_name == 'base64_file':
        base64_file = t.Base64File(**field_value)

        crop_size = CropSize(
                top=base64_file.top,
                left=base64_file.left)
        uuid = secrets.token_hex(32)
        blurhash_ = compute_blurhash(base64_file.image, crop_size=crop_size)
        extra_exts = ['gif'] if base64_file.image.format == 'GIF' else []

        params = dict(
            email=s.email,
            position=base64_file.position,
            uuid=uuid,
            blurhash=blurhash_,
            extra_exts=extra_exts,
            hash=base64_file.md5_hash,
        )

        # Create new onboardee photos. Because we:
        #   1. Create DB entries; then
        #   2. Create photos,
        # the DB might refer to DB entries that don't exist. The front end needs
        # to handle that possibility. Doing it like this makes later deletion
        # from the object store easier, which is important because storing
        # objects is expensive.
        q_set_onboardee_field = """
            WITH existing_uuid AS (
                SELECT
                    uuid
                FROM
                    onboardee_photo
                WHERE
                    email = %(email)s
                AND
                    position = %(position)s
            ), undeleted_photo_insertion AS (
                INSERT INTO undeleted_photo (
                    uuid
                )
                SELECT
                    uuid
                FROM
                    existing_uuid
            ), onboardee_photo_insertion AS (
                INSERT INTO onboardee_photo (
                    email,
                    position,
                    uuid,
                    blurhash,
                    extra_exts,
                    hash
                ) VALUES (
                    %(email)s,
                    %(position)s,
                    %(uuid)s,
                    %(blurhash)s,
                    %(extra_exts)s,
                    %(hash)s
                ) ON CONFLICT (email, position) DO UPDATE SET
                    uuid = EXCLUDED.uuid,
                    blurhash = EXCLUDED.blurhash,
                    extra_exts = EXCLUDED.extra_exts
            )
            SELECT 1
            """

        with api_tx() as tx:
            tx.execute(q_set_onboardee_field, params)

        try:
            put_image_in_object_store(uuid, base64_file, crop_size)
        except Exception as e:
            print('Upload failed with exception:', e)
            return '', 500

    else:
        return f'Invalid field name {field_name}', 400

def delete_onboardee_info(req: t.DeleteOnboardeeInfo, s: t.SessionInfo):
    params = [
        dict(email=s.email, position=position)
        for position in req.files
    ]

    with api_tx() as tx:
        tx.executemany(Q_DELETE_ONBOARDEE_PHOTO, params)

def post_finish_onboarding(s: t.SessionInfo):
    api_params = dict(
        email=s.email,
        normalized_email=normalize_email(s.email),
        pending_club_name=s.pending_club_name,
    )

    with api_tx() as tx:
        tx.execute('SET LOCAL statement_timeout = 15000') # 15 seconds
        tx.execute(Q_FINISH_ONBOARDING, params=api_params)
        row = tx.fetchone()

        # Referral credits — see docs/superpowers/specs/2026-06-05-beta-referrals-design.md.
        # Both calls are idempotent; harmless when the user has no
        # referral relationships in either direction.
        new_uuid = str(row['person_uuid'])
        credit_pending_for_invitee(tx, s.email, new_uuid)
        credit_pending_for_inviter(tx, s.email, new_uuid)

        club_params = dict(
            person_id=row['person_id'],
            club_name=s.pending_club_name,
            pending_club_name=s.pending_club_name,
            do_modify=True,
        )

        if \
                club_params['person_id'] is not None and \
                club_params['club_name'] is not None:
            tx.execute(Q_JOIN_CLUB, club_params)
            tx.execute(Q_UPSERT_SEARCH_PREFERENCE_CLUB, club_params)

        clubs = tx.execute(Q_GET_SESSION_CLUBS, club_params).fetchone()

    chat_params = dict(
        person_id=row['person_id'],
        person_uuid=row['person_uuid'],
    )

    return dict(**row, **clubs)

def get_me(
    person_id_as_int: int | None = None,
    person_id_as_str: str | None = None,
    include_email: bool = True,
):
    """Returns the current user's basic profile for the /me endpoint.

    Task 0.3f (Q&A subsystem strip per audit) replaced the original
    personality-trait-bearing implementation with a minimal name + person_id
    response. The `personality` array is now empty; clients should not depend
    on its contents (they shouldn't anyway, post Q&A strip).

    `include_email`: only the authed /me path passes True. The public
    /me/<uuid> path passes False so an attacker who knows a UUID cannot
    harvest the address (audit Auth #6).
    """
    if person_id_as_int is None and person_id_as_str is None:
        raise ValueError('pass an arg, please')

    params = dict(
        person_id_as_int=person_id_as_int,
        person_id_as_str=person_id_as_str,
    )

    with api_tx('READ COMMITTED') as tx:
        row = tx.execute(
            """
            SELECT
                id AS person_id,
                name AS person_name,
                uuid::TEXT AS person_uuid,
                email,
                primary_language
            FROM person
            WHERE
                (%(person_id_as_int)s::INT IS NOT NULL AND id = %(person_id_as_int)s::INT)
                OR
                (%(person_id_as_str)s::TEXT IS NOT NULL AND uuid::TEXT = %(person_id_as_str)s::TEXT)
            LIMIT 1
            """,
            params,
        ).fetchone()

    if not row:
        return '', 404

    out = {
        'name': row['person_name'],
        'person_id': row['person_id'],
        # The chat WebSocket SASL flow needs the bare uuid; /check-otp only
        # returns person_uuid for accounts that already had a person row at
        # OTP time (i.e. NOT fresh onboardees). Returning it here lets the
        # frontend backfill `ahavah.my-uuid` on first /me after graduation.
        'person_uuid': row['person_uuid'],
        'primary_language': row.get('primary_language'),
        'personality': [],   # populated when matching system relands in Phase 1+
    }
    if include_email:
        # Account-management surface fields. The frontend's
        # /settings/account renders these as the current values; without
        # them we showed hardcoded fakes ("ehud@example.com", etc.).
        out['email'] = row['email']
    return out

def get_prospect_profile(s: Optional[t.SessionInfo], prospect_uuid):
    params = dict(
        person_id=s.person_id if s is not None else None,
        prospect_uuid=prospect_uuid,
    )

    with api_tx('READ COMMITTED') as tx:
        api_row = tx.execute(Q_SELECT_PROSPECT_PROFILE, params).fetchone()
        if not api_row:
            return '', 404

        profile = api_row.get('j')
        if not profile:
            return '', 404

    if s is None:
        # Reply-rate stats count replies *to* %(person_id)s, so they're
        # meaningless for anonymous viewers - return NULL rather than 0%.
        profile.update(dict(
            gets_reply_percentage=None,
            gives_reply_percentage=None,
        ))
        return profile

    # Timeout in case someone with lots of messages hogs CPU time
    try:
        with api_tx('READ COMMITTED') as tx:
            tx.execute('SET LOCAL statement_timeout = 1000') # 1 second

            message_stats = tx.execute(Q_MESSAGE_STATS, params).fetchone()
    except psycopg.errors.QueryCanceled:
        message_stats = dict(
            gets_reply_percentage=None,
            gives_reply_percentage=None,
        )

    profile.update(message_stats)

    return profile

def get_conversation_prospect(s: t.SessionInfo, prospect_uuid: str):
    params = dict(
        person_id=s.person_id,
        prospect_uuid=prospect_uuid,
    )

    with api_tx('READ COMMITTED') as tx:
        api_row = tx.execute(
            Q_SELECT_CONVERSATION_PROSPECT, params
        ).fetchone()
        if not api_row:
            return '', 404

        profile = api_row.get('j')
        if not profile:
            return '', 404

        return profile

def post_skip_by_uuid(req: t.PostSkip, s: t.SessionInfo, prospect_uuid: str):
    if not s.person_uuid:
        return 'Authentication required', 401

    skip_by_uuid(
        subject_uuid=s.person_uuid,
        object_uuid=prospect_uuid,
        reason=req.report_reason or '',
    )


def post_unskip(s: t.SessionInfo, prospect_person_id: int):
    params = dict(
        subject_person_id=s.person_id,
        object_person_id=prospect_person_id,
    )

    with api_tx() as tx:
        tx.execute(Q_DELETE_SKIPPED, params)

def post_unskip_by_uuid(s: t.SessionInfo, prospect_uuid: str):
    params = dict(
        subject_person_id=s.person_id,
        prospect_uuid=prospect_uuid,
    )

    with api_tx() as tx:
        tx.execute(Q_DELETE_SKIPPED_BY_UUID, params)



def post_inbox_info(req: t.PostInboxInfo, s: t.SessionInfo):
    params = dict(
        person_id=s.person_id,
        prospect_person_uuids=req.person_uuids
    )

    with api_tx('READ COMMITTED') as tx:
        return tx.execute(Q_INBOX_INFO, params).fetchall()

def delete_or_ban_account(
    s: Optional[t.SessionInfo],
    admin_ban_token: Optional[str] = None,
):
    """Soft-delete with 7-day grace (Phase W cutover, migration 0008).

    For self-initiated deletes (`s` provided, no `admin_ban_token`): we
    flip `activated=false` and stamp `deletion_requested_at = NOW()`.
    The user vanishes from /search + /matches + /profile/[uuid]
    immediately (those queries filter by activated=true). The
    `pendingdeletion` cron hard-deletes after 7 days.

    For admin bans (`admin_ban_token` set): we still hard-delete via
    Q_ADMIN_BAN — admins act on policy violations and shouldn't have a
    grace window.
    """
    with api_tx() as tx:
        tx.execute('SET LOCAL statement_timeout = 30_000')  # 30 seconds

        if admin_ban_token:
            rows = tx.execute(
                Q_ADMIN_BAN,
                params=dict(token=admin_ban_token)
            ).fetchall()
            # Admin path: immediate hard-delete, no grace.
            tx.executemany(Q_DELETE_ACCOUNT, params_seq=rows)
        elif s:
            rows = [
                dict(
                    person_id=s.person_id,
                    person_uuid=s.person_uuid
                )
            ]
            # User-initiated: soft-delete only. Cron will hard-delete
            # after the 7-day grace window expires.
            cur = tx.execute(
                """
                UPDATE person
                   SET activated = FALSE,
                       deletion_requested_at = NOW()
                 WHERE id = %(person_id)s
                RETURNING email, name, deletion_requested_at
                """,
                dict(person_id=s.person_id),
            )
            _email_row = cur.fetchone()
            # Wipe every duo_session for this person so a token stolen
            # before the delete can no longer authenticate during the
            # 7-day grace window (audit Auth #2). User re-authenticates
            # via /request-otp on cancel-deletion.
            tx.execute(
                Q_DELETE_DUO_SESSIONS_FOR_PERSON,
                dict(person_id=s.person_id),
            )
        else:
            raise ValueError('At least one parameter must not be None')

    # Notify the user out-of-band so they have a recovery path even if
    # they close the app immediately. Threaded so the SMTP round-trip
    # doesn't block the DELETE response (Resend is usually <500ms but
    # we don't want the client waiting on it).
    if s and _email_row:
        try:
            import threading
            threading.Thread(
                target=_send_deletion_pending_email,
                kwargs=dict(
                    email=_email_row['email'],
                    name=_email_row['name'],
                    deletion_requested_at=_email_row['deletion_requested_at'],
                ),
                daemon=True,
            ).start()
        except Exception:
            import traceback
            print('delete_or_ban_account: deletion-email dispatch failed:')
            print(traceback.format_exc())

    return rows


def _send_deletion_pending_email(email: str, name: str, deletion_requested_at):
    """Worker for the threaded email dispatch in delete_or_ban_account.

    Adds 7 days to `deletion_requested_at` (UTC) to compute the purge
    cutoff, formats it for human consumption, builds the HTML body via
    service.person.deletion_email.deletion_pending_template, and sends
    via aws_smtp (Resend HTTPS in prod). Suppresses sample/example.com
    addresses so the autodeactivate2 convention is preserved."""
    if not email:
        return
    if email.lower().endswith('@example.com'):
        return

    try:
        from datetime import timedelta
        from smtp import aws_smtp
        from service.person.deletion_email import deletion_pending_template
        from service.config import PRODUCT_NAME

        purge_at = deletion_requested_at + timedelta(days=7)
        purge_pretty = purge_at.strftime('%a, %B %-d, %Y')

        body = deletion_pending_template(name=name, purge_iso=purge_pretty)
        aws_smtp.send(
            subject=f'Your {PRODUCT_NAME} account is scheduled for deletion',
            body=body,
            to_addr=email,
        )
        from emails.base import mask_email
        print(f'delete_or_ban_account: deletion email sent to {mask_email(email)}')
    except Exception:
        import traceback
        print('_send_deletion_pending_email: failed:')
        print(traceback.format_exc())

def cancel_account_deletion(s: t.SessionInfo):
    """Restore an account that's mid-grace-window (Phase W cutover).

    Counterpart to delete_or_ban_account's user-initiated branch:
    flips activated back to TRUE and clears deletion_requested_at,
    so the pendingdeletion cron stops considering the row for hard
    delete + the user reappears in /search + /matches.

    Idempotent — calling on a never-deleted account is a no-op
    (activated stays TRUE, deletion_requested_at stays NULL).

    Returns:
      {"ok": True, "restored": bool}  — restored=True if a pending
      deletion was actually canceled; False if there was nothing to
      cancel (already-active account).
    """
    if not s or not s.person_id:
        return 'Not authorized', 401

    with api_tx() as tx:
        cur = tx.execute(
            """
            UPDATE person
               SET activated = TRUE,
                   deletion_requested_at = NULL
             WHERE id = %(person_id)s
               AND deletion_requested_at IS NOT NULL
            RETURNING id
            """,
            dict(person_id=s.person_id),
        )
        rows = cur.fetchall()

    return {'ok': True, 'restored': len(rows) > 0}


def post_deactivate(s: t.SessionInfo):
    params = dict(person_id=s.person_id)

    with api_tx() as tx:
        tx.execute(Q_POST_DEACTIVATE, params)

# ---------------------------------------------------------------------------
# Change-email flow (Phase W cutover, migration 0007)
# ---------------------------------------------------------------------------
#
# Two-step OTP-to-new-email pattern. Step 1 stages the change on the
# person row + sends an OTP to the new address. Step 2 verifies the OTP
# and swaps. The user's existing session_token stays valid throughout
# (we don't sign them out), but a fresh OTP must be requested to confirm.
#
# Anti-abuse: 15-minute OTP expiry; a new request overwrites any prior
# pending change; uniqueness on pending_email so two users can't race
# for the same address.

CHANGE_EMAIL_OTP_TTL_MINUTES = 15

def change_email_request(s: t.SessionInfo, new_email: str):
    new_email = new_email.strip().lower()
    if not new_email or '@' not in new_email or len(new_email) > 320:
        return 'Invalid email address', 400

    # Block disposable / known-bad domains using the same filter as signup.
    if not check_and_update_bad_domains(new_email):
        return 'This email provider is not supported', 400

    # Reject if the new email is already someone's primary OR pending.
    with api_tx() as tx:
        existing = tx.execute(
            """
            SELECT 1 FROM person
            WHERE (email = %(e)s OR pending_email = %(e)s)
              AND id <> %(person_id)s
            LIMIT 1
            """,
            dict(e=new_email, person_id=s.person_id),
        ).fetchone()
    if existing:
        return 'This email is already in use', 409

    # Reject "no-op" change.
    if s.email and new_email == s.email.strip().lower():
        return 'New email matches your current email', 400

    otp = secrets.token_hex(3).upper()[:6]  # 6 hex chars, matches /request-otp UX
    otp_hash = sha512(otp)

    with api_tx() as tx:
        tx.execute(
            """
            UPDATE person
               SET pending_email = %(new_email)s,
                   pending_email_otp_hash = %(otp_hash)s,
                   pending_email_otp_expiry = NOW() + INTERVAL '%(ttl)s minutes'
             WHERE id = %(person_id)s
            """,
            dict(
                new_email=new_email,
                otp_hash=otp_hash,
                ttl=CHANGE_EMAIL_OTP_TTL_MINUTES,
                person_id=s.person_id,
            ),
        )

    _send_otp(new_email, otp)
    return dict(pending_email=new_email)

def change_email_verify(s: t.SessionInfo, otp: str):
    otp = otp.strip().upper()
    if not otp or len(otp) > 12:
        return 'Invalid code', 400

    otp_hash = sha512(otp)

    with api_tx() as tx:
        row = tx.execute(
            """
            SELECT pending_email, pending_email_otp_hash, pending_email_otp_expiry
            FROM person
            WHERE id = %(person_id)s
            """,
            dict(person_id=s.person_id),
        ).fetchone()

        if not row or not row.get('pending_email') or not row.get('pending_email_otp_hash'):
            return 'No pending email change', 400

        if row['pending_email_otp_hash'] != otp_hash:
            return 'Incorrect code', 400

        expiry = row.get('pending_email_otp_expiry')
        if expiry is None:
            return 'No pending email change', 400
        # psycopg returns datetime; compare against NOW() in SQL to avoid
        # timezone-aware/naive mismatches.
        expired = tx.execute(
            """SELECT NOW() > %(expiry)s AS expired""",
            dict(expiry=expiry),
        ).fetchone()
        if expired and expired.get('expired'):
            tx.execute(
                """
                UPDATE person SET
                  pending_email = NULL,
                  pending_email_otp_hash = NULL,
                  pending_email_otp_expiry = NULL
                WHERE id = %(person_id)s
                """,
                dict(person_id=s.person_id),
            )
            return 'Code expired — request a new one', 400

        new_email = row['pending_email']

        # Atomic swap: set email to pending_email, clear pending fields.
        # ON CONFLICT shouldn't fire because change_email_request already
        # checked uniqueness, but the unique index on person.email
        # protects us if two requests race.
        tx.execute(
            """
            UPDATE person SET
              email = %(new_email)s,
              normalized_email = %(normalized_email)s,
              pending_email = NULL,
              pending_email_otp_hash = NULL,
              pending_email_otp_expiry = NULL
            WHERE id = %(person_id)s
            """,
            dict(
                new_email=new_email,
                normalized_email=normalize_email(new_email),
                person_id=s.person_id,
            ),
        )
        # Email change implies an account-control event — wipe every OTHER
        # duo_session for this person so a stolen-pre-change token loses
        # access. Keep the caller's current session so the user stays
        # signed in on this device (audit Auth #2).
        tx.execute(
            Q_DELETE_DUO_SESSIONS_FOR_PERSON_EXCEPT,
            dict(
                person_id=s.person_id,
                keep_session_token_hash=s.session_token_hash,
            ),
        )

    return dict(email=new_email)

def get_profile_info(s: t.SessionInfo):
    params = dict(person_id=s.person_id)

    with api_tx('READ COMMITTED') as tx:
        return tx.execute(Q_GET_PROFILE_INFO, params).fetchone()['j']

def delete_profile_info(req: t.DeleteProfileInfo, s: t.SessionInfo):
    files_params = [
        dict(person_id=s.person_id, position=position)
        for position in req.files or []
    ]

    audio_files_params = [
        dict(person_id=s.person_id, position=-1)
        for position in req.audio_files or []
    ]

    if files_params:
        with api_tx() as tx:
            tx.executemany(Q_DELETE_PROFILE_INFO_PHOTO, files_params)
            tx.execute(Q_UPDATE_VERIFICATION_LEVEL, files_params[0])

    if audio_files_params:
        with api_tx() as tx:
            tx.executemany(Q_DELETE_PROFILE_INFO_AUDIO, audio_files_params)

def _patch_profile_info_about(person_id: int, new_about: str):
    select = """
    SELECT about AS old_about FROM person WHERE id = %(person_id)s
    """

    update = """
    WITH updated_person AS (
        UPDATE person
        SET
            about = %(new_about)s::TEXT,

            last_event_time =
                CASE
                    WHEN %(added_text)s::TEXT IS NULL
                    THEN sign_up_time
                    ELSE now()
                END,

            last_event_name =
                CASE
                    WHEN %(added_text)s::TEXT IS NULL
                    THEN 'joined'::person_event
                    ELSE 'updated-bio'::person_event
                END,

            last_event_data =
                CASE
                    WHEN %(added_text)s::TEXT IS NULL
                    THEN
                        '{}'::JSONB
                    ELSE
                        jsonb_build_object(
                            'added_text', %(added_text)s::TEXT,
                            'body_color', body_color,
                            'background_color', background_color
                        )
                END
        WHERE
            id = %(person_id)s
    ), updated_unmoderated_person AS (
        INSERT INTO
            unmoderated_person (person_id, trait)
        VALUES
            (%(person_id)s, 'about')
        ON CONFLICT DO NOTHING
    )
    SELECT 1
    """

    with api_tx() as tx:
        select_params = dict(
            person_id=person_id,
        )

        tx.execute(select, select_params)

        old_about = tx.fetchone()['old_about']

        update_params = dict(
            person_id=person_id,
            new_about=new_about,
            added_text=diff_addition_with_context(old=old_about, new=new_about),
        )

        tx.execute(update, update_params)

def patch_profile_info(req: t.PatchProfileInfo, s: t.SessionInfo):
    if not s.person_id:
        return 'Not authorized', 400

    [field_name] = req.__pydantic_fields_set__
    field_value = req.dict()[field_name]

    params = dict(
        person_id=s.person_id,
        field_value=field_value,
    )

    q1 = None
    q2 = None

    uuid = None
    base64_file = None
    crop_size = None

    base64_audio_file = None

    if field_name == 'base64_file':
        base64_file = t.Base64File(**field_value)

        crop_size = CropSize(
                top=base64_file.top,
                left=base64_file.left)
        uuid = secrets.token_hex(32)
        blurhash_ = compute_blurhash(base64_file.image, crop_size=crop_size)
        extra_exts = ['gif'] if base64_file.image.format == 'GIF' else []

        params = dict(
            person_id=s.person_id,
            position=base64_file.position,
            uuid=uuid,
            blurhash=blurhash_,
            extra_exts=extra_exts,
            hash=base64_file.md5_hash,
        )

        q1 = """
        WITH existing_uuid AS (
            SELECT
                uuid
            FROM
                photo
            WHERE
                person_id = %(person_id)s
            AND
                position = %(position)s
        ), undeleted_photo_insertion AS (
            INSERT INTO undeleted_photo (
                uuid
            )
            SELECT
                uuid
            FROM
                existing_uuid
        ), photo_insertion AS (
            INSERT INTO photo (
                person_id,
                position,
                uuid,
                blurhash,
                extra_exts,
                hash
            ) VALUES (
                %(person_id)s,
                %(position)s,
                %(uuid)s,
                %(blurhash)s,
                %(extra_exts)s,
                %(hash)s
            ) ON CONFLICT (person_id, position) DO UPDATE SET
                uuid = EXCLUDED.uuid,
                blurhash = EXCLUDED.blurhash,
                extra_exts = EXCLUDED.extra_exts,
                hash = EXCLUDED.hash,
                verified = FALSE
        ), updated_person AS (
            UPDATE person
            SET
                last_event_time = now(),
                last_event_name = 'added-photo',
                last_event_data = jsonb_build_object(
                    'added_photo_uuid', %(uuid)s,
                    'added_photo_blurhash', %(blurhash)s,
                    'added_photo_extra_exts', %(extra_exts)s::TEXT[]
                )
            WHERE
                id = %(person_id)s
        )
        SELECT 1
        """

        q2 = Q_UPDATE_VERIFICATION_LEVEL
    elif field_name == 'base64_audio_file':
        base64_audio_file = t.Base64AudioFile(**field_value)

        uuid = secrets.token_hex(32)

        params = dict(
            person_id=s.person_id,
            uuid=uuid,
        )

        q1 = """
        WITH existing_uuid AS (
            SELECT
                uuid
            FROM
                audio
            WHERE
                person_id = %(person_id)s
            AND
                position = -1
        ), undeleted_audio_insertion AS (
            INSERT INTO undeleted_audio (
                uuid
            )
            SELECT
                uuid
            FROM
                existing_uuid
        ), audio_insertion AS (
            INSERT INTO audio (
                person_id,
                position,
                uuid
            ) VALUES (
                %(person_id)s,
                -1,
                %(uuid)s
            ) ON CONFLICT (person_id, position) DO UPDATE SET
                uuid = EXCLUDED.uuid
        ), updated_person AS (
            UPDATE person
            SET
                last_event_time = now(),
                last_event_name = 'added-voice-bio',
                last_event_data = jsonb_build_object(
                    'added_audio_uuid', %(uuid)s
                )
            WHERE
                id = %(person_id)s
        )
        SELECT 1
        """
    elif field_name == 'photo_assignments':
        case_sql = '\n'.join(
            f'WHEN position = {int(k)} THEN {int(v)}'
            for k, v in field_value.items()
        )

        # We set the positions to negative indexes first, to avoid violating
        # uniqueness constraints
        q1 = f"""
        UPDATE
            photo
        SET
            position = - (CASE {case_sql} ELSE position END)
        WHERE
            person_id = %(person_id)s
        """

        q2 = """
        UPDATE
            photo
        SET
            position = ABS(position)
        WHERE
            person_id = %(person_id)s
        """
    elif field_name == 'name':
        # Ahavah change: removed the upstream Duolicious "Requires gold"
        # gate on display-name changes. The Torah-observant audience is
        # small + heavily vetted; rename-abuse is not a problem we have
        # AND blocking the rename made the optimistic UI on /profile/edit
        # silently revert (user reported it as a broken control).
        q1 = """
        UPDATE person
        SET name = %(field_value)s
        WHERE id = %(person_id)s
        """
    elif field_name == 'about':
        return _patch_profile_info_about(s.person_id, field_value)
    elif field_name == 'gender':
        q1 = """
        UPDATE person
        SET gender_id = gender.id, verified_gender = false
        FROM gender
        WHERE person.id = %(person_id)s
        AND gender.name = %(field_value)s
        AND person.gender_id <> gender.id
        """

        q2 = Q_UPDATE_VERIFICATION_LEVEL
    elif field_name == 'other_peoples_genders':
        # Ahavah change: replace the upstream "only on /onboardee-info"
        # restriction. Post-onboarded users can change their search
        # preference (e.g. via /profile/edit sex toggle fanning out to
        # {gender, other_peoples_genders}). DELETE+INSERT is the same
        # shape post_search_filter uses for its 'gender' branch.
        q1 = """
        DELETE FROM search_preference_gender
        WHERE person_id = %(person_id)s
        """
        q2 = """
        INSERT INTO search_preference_gender (person_id, gender_id)
        SELECT %(person_id)s, gender.id
        FROM gender
        WHERE gender.name = ANY(%(field_value)s)
        """
    elif field_name == 'ahavah_extra':
        # Merge the incoming JSON object into the stored blob — the
        # client sends partial patches (e.g. {assembly: "natsarim"})
        # and we keep every prior field intact. `||` is Postgres's
        # JSONB shallow-merge operator: right-hand keys overwrite.
        # field_value is already a python dict from pydantic; psycopg
        # adapts dict -> jsonb automatically when cast via Jsonb().
        # We use json.dumps + ::jsonb cast for portability across the
        # psycopg version pinned in the container.
        params = dict(
            person_id=s.person_id,
            field_value=json.dumps(field_value),
        )
        q1 = """
        UPDATE person
        SET ahavah_extra = ahavah_extra || %(field_value)s::jsonb
        WHERE id = %(person_id)s
        """
    elif field_name == 'orientation':
        q1 = """
        UPDATE person SET orientation_id = orientation.id
        FROM orientation
        WHERE person.id = %(person_id)s
        AND orientation.name = %(field_value)s
        """
    elif field_name == 'ethnicity':
        q1 = """
        UPDATE person
        SET ethnicity_id = ethnicity.id, verified_ethnicity = false
        FROM ethnicity
        WHERE person.id = %(person_id)s
        AND ethnicity.name = %(field_value)s
        AND person.ethnicity_id <> ethnicity.id
        """

        q2 = Q_UPDATE_VERIFICATION_LEVEL
    elif field_name == 'location':
        # Phase W map fix (2026-05-15): also populate person.country
        # (CHAR(2) ISO2) so /search ships ISO codes the frontend
        # WorldMap + centroidOf() expect. The location table only
        # stores the full country NAME ("Barbados", not "BB"), so we
        # resolve via pycountry on the way in. Without this, every
        # newly-onboarded user has country = '' and is filtered out
        # of the map view (see realCandidates.filter(c => Boolean(c.country))
        # in src/app/map/page.tsx).
        try:
            import pycountry
            with api_tx() as tx:
                _row = tx.execute(
                    "SELECT country FROM location WHERE long_friendly = %(lf)s",
                    dict(lf=field_value),
                ).fetchone()
            _country_name = (_row or {}).get('country') or ''
            _country_iso = ''
            if _country_name:
                try:
                    _country_iso = pycountry.countries.lookup(_country_name).alpha_2
                except LookupError:
                    _country_iso = ''
        except Exception:
            # pycountry missing or any unexpected lookup failure: leave
            # country empty so the rest of the location update still
            # succeeds. Map will hide the user; they can be backfilled
            # manually until pycountry lands in the image.
            _country_iso = ''

        params = dict(
            person_id=s.person_id,
            field_value=field_value,
            country_iso=_country_iso,
        )
        q1 = """
        UPDATE person
        SET
            coordinates
                = location.coordinates,

            verification_required
                = location.verification_required OR person.verification_required,

            location_short_friendly
                = location.short_friendly,

            location_long_friendly
                = location.long_friendly,

            country
                = COALESCE(NULLIF(%(country_iso)s, ''), person.country)
        FROM location
        WHERE person.id = %(person_id)s
        AND long_friendly = %(field_value)s
        """
    elif field_name == 'occupation':
        q1 = """
        UPDATE person SET occupation = %(field_value)s
        WHERE person.id = %(person_id)s
        """
    elif field_name == 'education':
        q1 = """
        UPDATE person SET education = %(field_value)s
        WHERE person.id = %(person_id)s
        """
    elif field_name == 'height':
        q1 = """
        UPDATE person SET height_cm = %(field_value)s
        WHERE person.id = %(person_id)s
        """
    elif field_name == 'looking_for':
        q1 = """
        UPDATE person SET looking_for_id = looking_for.id
        FROM looking_for
        WHERE person.id = %(person_id)s
        AND looking_for.name = %(field_value)s
        """
    elif field_name == 'country':
        # Phase W cutover (2026-05-15): direct ISO2 country PATCH so
        # /profile/edit country changes move the user's /search pool
        # (Q_UNCACHED_SEARCH_2 filters on p.country = ANY(preferred)).
        # Was previously only set via /onboarding/location's pycountry
        # path; post-onboarding edits silently dropped.
        # Validation: 2-char uppercase ISO2 only — anything else
        # leaves person.country unchanged (UPDATE no-ops by WHERE).
        q1 = """
        UPDATE person
           SET country = UPPER(%(field_value)s)
         WHERE id = %(person_id)s
           AND length(%(field_value)s) = 2
           AND %(field_value)s ~ '^[A-Za-z]{2}$'
        """
    elif field_name == 'languages_spoken':
        # Phase W: round-trip language multi-select. Frontend sends an
        # array of canonical codes (en, he, ...) plus optional
        # "custom:..." entries; stored verbatim in TEXT[] column.
        # Empty list clears the field — search query treats `[]` as
        # "no preference".
        q1 = """
        UPDATE person
           SET languages_spoken = %(field_value)s::TEXT[]
         WHERE id = %(person_id)s
        """
    elif field_name == 'primary_language':
        # Phase W: marks which of the user's `languages_spoken` entries
        # is their primary spoken language. Drives the ★ prefix on the
        # Languages cluster in /profile/[uuid]. Stored as-is; lookup-
        # table validation deferred (free text by design since custom-
        # language tokens like "custom:Aramaic" are valid).
        q1 = """
        UPDATE person
           SET primary_language = %(field_value)s
         WHERE id = %(person_id)s
        """
    elif field_name == 'verification_required':
        # "Require my matches to be verified" — a SEARCHER preference that
        # drives the discover verified_only filter (the frontend reads it and
        # passes ?verified_only). Backed by require_verified_prospects, NOT
        # person.verification_required: the latter is the anti-abuse / location
        # flag read by the chat send-gate + the global search hide, so writing
        # it here blocked the user's own messaging + hid them from search
        # (conflation bug fixed 2026-05-21). Value "Yes"/"No" matching the
        # show_my_age + sibling toggles.
        q1 = """
        UPDATE person
           SET require_verified_prospects = (
               CASE WHEN %(field_value)s = 'Yes' THEN TRUE ELSE FALSE END)
         WHERE id = %(person_id)s
        """
    elif field_name == 'smoking':
        q1 = """
        UPDATE person SET smoking_id = yes_no_optional.id
        FROM yes_no_optional
        WHERE person.id = %(person_id)s
        AND yes_no_optional.name = %(field_value)s
        """
    elif field_name == 'drinking':
        q1 = """
        UPDATE person SET drinking_id = frequency.id
        FROM frequency
        WHERE person.id = %(person_id)s
        AND frequency.name = %(field_value)s
        """
    elif field_name == 'drugs':
        q1 = """
        UPDATE person SET drugs_id = yes_no_optional.id
        FROM yes_no_optional
        WHERE person.id = %(person_id)s
        AND yes_no_optional.name = %(field_value)s
        """
    elif field_name == 'long_distance':
        q1 = """
        UPDATE person SET long_distance_id = yes_no_optional.id
        FROM yes_no_optional
        WHERE person.id = %(person_id)s
        AND yes_no_optional.name = %(field_value)s
        """
    elif field_name == 'relationship_status':
        q1 = """
        UPDATE person SET relationship_status_id = relationship_status.id
        FROM relationship_status
        WHERE person.id = %(person_id)s
        AND relationship_status.name = %(field_value)s
        """
    elif field_name == 'has_kids':
        q1 = """
        UPDATE person SET has_kids_id = yes_no_maybe.id
        FROM yes_no_maybe
        WHERE person.id = %(person_id)s
        AND yes_no_maybe.name = %(field_value)s
        """
    elif field_name == 'wants_kids':
        q1 = """
        UPDATE person SET wants_kids_id = yes_no_maybe.id
        FROM yes_no_maybe
        WHERE person.id = %(person_id)s
        AND yes_no_maybe.name = %(field_value)s
        """
    elif field_name == 'exercise':
        q1 = """
        UPDATE person SET exercise_id = frequency.id
        FROM frequency
        WHERE person.id = %(person_id)s
        AND frequency.name = %(field_value)s
        """
    elif field_name == 'religion':
        q1 = """
        UPDATE person SET religion_id = religion.id
        FROM religion
        WHERE person.id = %(person_id)s
        AND religion.name = %(field_value)s
        """
    elif field_name == 'star_sign':
        q1 = """
        UPDATE person SET star_sign_id = star_sign.id
        FROM star_sign
        WHERE person.id = %(person_id)s
        AND star_sign.name = %(field_value)s
        """
    elif field_name == 'units':
        q1 = """
        UPDATE person SET unit_id = unit.id
        FROM unit
        WHERE person.id = %(person_id)s
        AND unit.name = %(field_value)s
        """
    elif field_name == 'chats':
        q1 = """
        UPDATE person SET chats_notification = immediacy.id
        FROM immediacy
        WHERE person.id = %(person_id)s
        AND immediacy.name = %(field_value)s
        """
    elif field_name == 'intros':
        q1 = """
        UPDATE person SET intros_notification = immediacy.id
        FROM immediacy
        WHERE person.id = %(person_id)s
        AND immediacy.name = %(field_value)s
        """
    elif field_name == 'verification_level':
        q1 = """
        UPDATE person
        SET privacy_verification_level_id = verification_level.id
        FROM verification_level
        WHERE person.id = %(person_id)s AND
        verification_level.name = %(field_value)s
        """
    elif field_name == 'show_my_location':
        if not _has_gold(person_id=s.person_id):
            return 'Requires gold', 403

        q1 = """
        UPDATE person
        SET show_my_location = (
            CASE WHEN %(field_value)s = 'Yes' THEN TRUE ELSE FALSE END)
        WHERE id = %(person_id)s
        """
    elif field_name == 'show_my_age':
        if not _has_gold(person_id=s.person_id):
            return 'Requires gold', 403

        q1 = """
        UPDATE person
        SET show_my_age = (
            CASE WHEN %(field_value)s = 'Yes' THEN TRUE ELSE FALSE END)
        WHERE id = %(person_id)s
        """
    elif field_name == 'hide_me_from_strangers':
        if not _has_gold(person_id=s.person_id):
            return 'Requires gold', 403

        q1 = """
        UPDATE person
        SET hide_me_from_strangers = (
            CASE WHEN %(field_value)s = 'Yes' THEN TRUE ELSE FALSE END)
        WHERE id = %(person_id)s
        """
    elif field_name == 'browse_invisibly':
        if not _has_gold(person_id=s.person_id):
            return 'Requires gold', 403

        q1 = """
        UPDATE person
        SET browse_invisibly = (
            CASE WHEN %(field_value)s = 'Yes' THEN TRUE ELSE FALSE END)
        WHERE id = %(person_id)s
        """
    elif field_name == 'public_profile':
        q1 = """
        UPDATE person
        SET public_profile = (
            CASE WHEN %(field_value)s = 'Yes' THEN TRUE ELSE FALSE END)
        WHERE id = %(person_id)s
        """
    elif field_name == 'theme':
        if not _has_gold(person_id=s.person_id):
            return 'Requires gold', 403

        try:
            title_color = field_value['title_color']
            body_color = field_value['body_color']
            background_color = field_value['background_color']

            params.update(
                dict(
                    title_color=title_color,
                    body_color=body_color,
                    background_color=background_color,
                )
            )
        except:
            return f'Invalid colors', 400

        q1 = """
        UPDATE person
        SET
            title_color = %(title_color)s,
            body_color = %(body_color)s,
            background_color = %(background_color)s
        WHERE id = %(person_id)s
        """
    else:
        return f'Unhandled field name {field_name}', 500

    with api_tx() as tx:
        if q1: tx.execute(q1, params)
        if q2: tx.execute(q2, params)

    if uuid and base64_file and crop_size:
        try:
            put_image_in_object_store(uuid, base64_file, crop_size)
        except:
            print(traceback.format_exc())
            return '', 500

    if uuid and base64_audio_file:
        try:
            put_audio_in_object_store(
                uuid=uuid,
                audio_file_bytes=base64_audio_file.transcoded,
            )
        except:
            print(traceback.format_exc())
            return '', 500

def get_search_filters(s: t.SessionInfo):
    return get_search_filters_by_person_id(person_id=s.person_id)

def get_search_filters_by_person_id(person_id: Optional[int]):
    params = dict(person_id=person_id)

    with api_tx('READ COMMITTED') as tx:
        return tx.execute(Q_GET_SEARCH_FILTERS, params).fetchone()['j']

def post_search_filter(req: t.PostSearchFilter, s: t.SessionInfo):
    [field_name] = req.__pydantic_fields_set__
    field_value = req.dict()[field_name]

    # Modify `field_value` for certain `field_name`s
    if field_name in ['age', 'height']:
        field_value = json.dumps(field_value)

    params = dict(
        person_id=s.person_id,
        field_value=field_value,
    )

    with api_tx() as tx:
        if field_name == 'gender':
            q1 = """
            DELETE FROM search_preference_gender
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_gender (
                person_id, gender_id
            )
            SELECT %(person_id)s, id
            FROM gender WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'orientation':
            q1 = """
            DELETE FROM search_preference_orientation
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_orientation (
                person_id, orientation_id
            )
            SELECT %(person_id)s, id
            FROM orientation WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'ethnicity':
            q1 = """
            DELETE FROM search_preference_ethnicity
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_ethnicity (
                person_id, ethnicity_id
            )
            SELECT %(person_id)s, id
            FROM ethnicity WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'age':
            q1 = """
            DELETE FROM search_preference_age
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_age (
                person_id, min_age, max_age
            ) SELECT
                %(person_id)s,
                (json_data->>'min_age')::SMALLINT,
                (json_data->>'max_age')::SMALLINT
            FROM to_json(%(field_value)s::json) AS json_data"""
        elif field_name == 'furthest_distance':
            q1 = """
            DELETE FROM search_preference_distance
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_distance (person_id, distance)
            VALUES (%(person_id)s, %(field_value)s)
            """
        elif field_name == 'height':
            q1 = """
            DELETE FROM search_preference_height_cm
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_height_cm (
                person_id, min_height_cm, max_height_cm
            ) SELECT
                %(person_id)s,
                (json_data->>'min_height_cm')::SMALLINT,
                (json_data->>'max_height_cm')::SMALLINT
            FROM to_json(%(field_value)s::json) AS json_data"""
        elif field_name == 'has_a_profile_picture':
            q1 = """
            DELETE FROM search_preference_has_profile_picture
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_has_profile_picture (
                person_id, has_profile_picture_id
            ) SELECT %(person_id)s, id
            FROM yes_no WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'looking_for':
            q1 = """
            DELETE FROM search_preference_looking_for
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_looking_for (
                person_id, looking_for_id
            ) SELECT %(person_id)s, id
            FROM looking_for WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'smoking':
            q1 = """
            DELETE FROM search_preference_smoking
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_smoking (
                person_id, smoking_id
            )
            SELECT %(person_id)s, id
            FROM yes_no_optional WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'drinking':
            q1 = """
            DELETE FROM search_preference_drinking
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_drinking (
                person_id, drinking_id
            )
            SELECT %(person_id)s, id
            FROM frequency WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'drugs':
            q1 = """
            DELETE FROM search_preference_drugs
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_drugs (
                person_id, drugs_id
            )
            SELECT %(person_id)s, id
            FROM yes_no_optional WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'long_distance':
            q1 = """
            DELETE FROM search_preference_long_distance
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_long_distance (
                person_id, long_distance_id
            )
            SELECT %(person_id)s, id
            FROM yes_no_optional WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'relationship_status':
            q1 = """
            DELETE FROM search_preference_relationship_status
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_relationship_status (
                person_id, relationship_status_id
            )
            SELECT %(person_id)s, id
            FROM relationship_status WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'has_kids':
            q1 = """
            DELETE FROM search_preference_has_kids
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_has_kids (
                person_id, has_kids_id
            )
            SELECT %(person_id)s, id
            FROM yes_no_optional WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'wants_kids':
            q1 = """
            DELETE FROM search_preference_wants_kids
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_wants_kids (
                person_id, wants_kids_id
            )
            SELECT %(person_id)s, id
            FROM yes_no_maybe WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'exercise':
            q1 = """
            DELETE FROM search_preference_exercise
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_exercise (
                person_id, exercise_id
            )
            SELECT %(person_id)s, id
            FROM frequency WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'religion':
            q1 = """
            DELETE FROM search_preference_religion
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_religion (
                person_id, religion_id
            )
            SELECT %(person_id)s, id
            FROM religion WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'star_sign':
            q1 = """
            DELETE FROM search_preference_star_sign
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_star_sign (
                person_id, star_sign_id
            )
            SELECT %(person_id)s, id
            FROM star_sign WHERE name = ANY(%(field_value)s)
            """
        elif field_name == 'people_you_messaged':
            q1 = """
            DELETE FROM search_preference_messaged
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_messaged (
                person_id, messaged_id
            )
            SELECT %(person_id)s, id
            FROM yes_no WHERE name = %(field_value)s
            """
        elif field_name == 'people_you_skipped':
            q1 = """
            DELETE FROM search_preference_skipped
            WHERE person_id = %(person_id)s"""

            q2 = """
            INSERT INTO search_preference_skipped (
                person_id, skipped_id
            )
            SELECT %(person_id)s, id
            FROM yes_no WHERE name = %(field_value)s
            """
        else:
            return f'Invalid field name {field_name}', 400

        tx.execute(q1, params)
        tx.execute(q2, params)


def get_search_clubs(
        s: Optional[t.SessionInfo],
        search_str: str,
        allow_empty: bool = False):

    lower_search_str = search_str.lower().strip()

    if allow_empty and not lower_search_str:
        pass
    elif not re.match(t.CLUB_PATTERN, lower_search_str):
        return []
    elif not len(lower_search_str) <= t.CLUB_MAX_LEN:
        return []

    params = dict(
        person_id=s.person_id if s else None,
        search_string=lower_search_str,
    )

    q = Q_SEARCH_CLUBS if lower_search_str else Q_TOP_CLUBS

    with api_tx('READ COMMITTED') as tx:
        return tx.execute(q, params).fetchall()

def post_join_club(req: t.PostJoinClub, s: t.SessionInfo):
    params = dict(
        person_id=s.person_id,
        club_name=req.name,
    )

    with api_tx() as tx:
        rows = tx.execute(Q_JOIN_CLUB, params).fetchall()

    if rows:
        return f"Joined {req.name}", 200
    else:
        return f"Couldn't join {req.name}", 400

def post_leave_club(req: t.PostLeaveClub, s: t.SessionInfo):
    params = dict(
        person_id=s.person_id,
        club_name=req.name,
    )

    with api_tx() as tx:
        tx.execute(Q_LEAVE_CLUB, params)

def get_update_notifications(email: str, type: str, frequency: str):
    params = dict(
        email=email,
        frequency=frequency,
    )

    if type == 'Intros':
        queries = [Q_UPDATE_INTROS_NOTIFICATIONS]
    elif type == 'Chats':
        queries = [Q_UPDATE_CHATS_NOTIFICATIONS]
    elif type == 'Every':
        queries = [Q_UPDATE_INTROS_NOTIFICATIONS, Q_UPDATE_CHATS_NOTIFICATIONS]
    else:
        return 'Invalid type', 400

    with api_tx('READ COMMITTED') as tx:
        query_results = [tx.execute(q, params).fetchone()['ok'] for q in queries]

    if all(query_results):
        return (
            f"✅ "
            f"<b>{type}</b> notification frequency set to "
            f"<b>{frequency}</b> for "
            f"<b>{email}</b>")
    else:
        return 'Invalid email address or notification frequency', 400

def post_verification_selfie(req: t.PostVerificationSelfie, s: t.SessionInfo):
    base64 = req.base64_file.base64
    image = req.base64_file.image
    top = req.base64_file.top
    left = req.base64_file.left
    hash = req.base64_file.md5_hash

    crop_size = CropSize(top=top, left=left)
    photo_uuid = secrets.token_hex(32)

    params_ok = dict(
        person_id=s.person_id,
        photo_uuid=photo_uuid,
        photo_hash=hash,
        expected_previous_status=None,
    )

    params_bad = dict(
        person_id=s.person_id,
        status='failure',
        message=V_REUSED_SELFIE,
        expected_previous_status=None,
    )

    with api_tx() as tx:
        if tx.execute(Q_INSERT_VERIFICATION_PHOTO_HASH, params_ok).fetchall():
            tx.execute(Q_DELETE_VERIFICATION_JOB, params_ok)
            tx.execute(Q_INSERT_VERIFICATION_JOB, params_ok)
        else:
            tx.execute(Q_UPDATE_VERIFICATION_JOB, params_bad)

    try:
        put_image_in_object_store(
            photo_uuid, req.base64_file, crop_size, sizes=[450])
    except Exception as e:
        print('Upload failed with exception:', e)
        return '', 500

def post_verification_multi_selfie(
    req: t.PostVerificationMultiSelfie,
    s: t.SessionInfo,
):
    """Silver-tier capture endpoint. Mirrors post_verification_selfie's
    upload-then-job pattern but stores 3 frames in one shot and writes
    silver_burst_uuids on the resulting verification_job. The cron then
    picks it up via the same /verify -> 'queued' status flip; the
    burst_uuids drive the multi-frame classifier path.

    Hash check: each frame must be unique against verification_photo_hash
    (same anti-replay as Bronze). If any frame is a reuse the whole
    burst is failed before the job is inserted, so the user sees the
    same V_REUSED_SELFIE error they'd get on Bronze.
    """
    if len(req.frames) != 3:
        return 'frames must contain exactly 3 entries', 400

    photo_uuids = [secrets.token_hex(32) for _ in req.frames]
    proof_uuid = photo_uuids[0]
    burst_uuids = photo_uuids[1:]

    with api_tx() as tx:
        # Anti-replay: every frame must clear the dedupe table. If any
        # one fails, mark the job 'failure' so the user retries with
        # fresh captures (same UX as Bronze).
        for frame in req.frames:
            row = tx.execute(
                Q_INSERT_VERIFICATION_PHOTO_HASH,
                dict(photo_hash=frame.md5_hash),
            ).fetchall()
            if not row:
                tx.execute(Q_UPDATE_VERIFICATION_JOB, dict(
                    person_id=s.person_id,
                    status='failure',
                    message=V_REUSED_SELFIE,
                    expected_previous_status=None,
                ))
                return '', 200

        # Replace any prior verification_job for this person, then
        # insert the silver burst. Reusing the existing INSERT and
        # then UPDATEing silver_burst_uuids in a follow-up statement
        # keeps Q_INSERT_VERIFICATION_JOB unchanged.
        tx.execute(Q_DELETE_VERIFICATION_JOB, dict(person_id=s.person_id))
        tx.execute(Q_INSERT_VERIFICATION_JOB, dict(
            person_id=s.person_id,
            photo_uuid=proof_uuid,
        ))
        tx.execute(
            """
            UPDATE verification_job
               SET silver_burst_uuids = %(burst_uuids)s::TEXT[]
             WHERE person_id = %(person_id)s
            """,
            dict(person_id=s.person_id, burst_uuids=burst_uuids),
        )

    # Upload all three frames to the bucket. Failure here is rare but
    # non-fatal to the row (cron will fail the job naturally when the
    # classifier can't fetch the image). 500 keeps the user's UI in
    # the "uploading-photo" state so they can retry.
    try:
        for uuid_, frame in zip(photo_uuids, req.frames):
            put_image_in_object_store(
                uuid_,
                frame,
                CropSize(top=frame.top, left=frame.left),
                sizes=[450],
            )
    except Exception as e:
        print('Multi-selfie upload failed with exception:', e)
        return '', 500


def post_verify(s: t.SessionInfo):
    params = dict(
        person_id=s.person_id,
        status='queued',
        message=V_QUEUED,
        expected_previous_status='uploading-photo',
    )

    with api_tx() as tx:
        tx.execute(Q_UPDATE_VERIFICATION_JOB, params)

def get_check_verification(s: t.SessionInfo):
    with api_tx() as tx:
        row = tx.execute(
            Q_CHECK_VERIFICATION,
            dict(person_id=s.person_id)
        ).fetchone()

    if row:
        return row
    return '', 400

def post_dismiss_donation(s: t.SessionInfo):
    with api_tx() as tx:
        tx.execute(Q_DISMISS_DONATION, dict(person_id=s.person_id))

@lru_cache()
def get_stats(ttl_hash=None, club_name: Optional[str] = None):
    if club_name:
        q, params = Q_STATS_BY_CLUB_NAME, dict(club_name=club_name)
    else:
        q, params = Q_STATS, None

    with api_tx('READ COMMITTED') as tx:
        return tx.execute(q, params).fetchone()

@lru_cache()
def get_gender_stats(ttl_hash=None):
    with api_tx('READ COMMITTED') as tx:
        return tx.execute(Q_GENDER_STATS).fetchone()

def _confirm_form_html(action_path: str, token: str, button_label: str) -> str:
    """Tiny confirmation form. GET serves this; POST performs the action.
    Splitting prevents link-warmers (Gmail prefetcher, Microsoft SafeLinks,
    corporate antivirus URL scanners) from firing the destructive action
    by prefetching the GET URL before the admin manually clicks (audit
    Auth #7). The POST requires an actual click."""
    safe_token = html_escape(token)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="robots" content="noindex, nofollow"/>
<title>Confirm admin action</title>
<style>body{{font-family:system-ui,sans-serif;background:#0a0a0a;color:#f4f4f5;display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}}
form{{background:#111114;border:1px solid rgba(255,255,255,0.08);border-radius:14px;padding:28px;max-width:420px;width:100%}}
button{{margin-top:14px;width:100%;padding:14px;border:0;border-radius:10px;background:#f4f4f5;color:#0a0a0a;font-weight:700;font-size:15px;cursor:pointer}}
button:hover{{background:#fff}}p{{margin:0 0 8px;color:#a1a1aa;font-size:13px}}</style></head>
<body><form method="post" action="{action_path}/{safe_token}">
<p>You're about to perform an irreversible admin action.</p>
<p>Token: <code>{safe_token}</code></p>
<button type="submit">Confirm</button>
</form></body></html>"""


def get_admin_ban_link(token: str):
    params = dict(token=token)

    err_invalid_token = (
        'Invalid token. User might have already been banned', 401)

    try:
        with api_tx() as tx:
            person_uuid = tx.execute(
                Q_ADMIN_TOKEN_TO_UUID,
                params,
            ).fetchone()['person_uuid']
    except TypeError:
        return err_invalid_token

    try:
        with api_tx('READ COMMITTED') as tx:
            rows = tx.execute(Q_CHECK_ADMIN_BAN_TOKEN, params).fetchall()
    except psycopg.errors.InvalidTextRepresentation:
        return err_invalid_token

    if rows:
        # Render a POST-form confirmation page instead of an anchor link.
        # The action endpoint /admin/ban now only fires on POST so link-
        # prefetchers can't trigger the ban (audit Auth #7).
        return _confirm_form_html('/admin/ban', token, 'Confirm ban')
    else:
        return err_invalid_token


def post_admin_ban(token: str):
    """POST /admin/ban/<token> — destructive. GET on this path is rejected
    (the confirmation form lives at /admin/ban-link/<token>)."""
    rows = delete_or_ban_account(s=None, admin_ban_token=token)
    if rows:
        return f'Banned {rows}'
    else:
        return 'Ban failed; User already banned or token invalid', 401


def get_admin_delete_photo_link(token: str):
    params = dict(token=token)

    try:
        with api_tx('READ COMMITTED') as tx:
            tx.execute(Q_CHECK_ADMIN_DELETE_PHOTO_TOKEN, params)
            rows = tx.fetchall()
    except psycopg.errors.InvalidTextRepresentation:
        return 'Invalid token', 401

    if rows:
        return _confirm_form_html('/admin/delete-photo', token, 'Confirm delete')
    else:
        return 'Invalid token', 401


def post_admin_delete_photo(token: str):
    """POST /admin/delete-photo/<token> — destructive. GET is rejected."""
    params = dict(token=token)

    with api_tx('READ COMMITTED') as tx:
        rows = tx.execute(Q_ADMIN_DELETE_PHOTO, params).fetchall()

        if rows:
            params = dict(person_id=rows[0]['person_id'])
            tx.execute(Q_UPDATE_VERIFICATION_LEVEL, params)

    if rows:
        return f'Deleted photo {rows}'
    else:
        return 'Photo deletion failed', 401

def get_export_data_token(s: t.SessionInfo):
    params = dict(person_id=s.person_id)

    with api_tx() as tx:
        return tx.execute(Q_INSERT_EXPORT_DATA_TOKEN, params).fetchone()

def get_export_data(token: str):
    token_params = dict(token=token)

    # Fetch data from database
    with api_tx('read committed') as tx:
        params = tx.execute(Q_CHECK_EXPORT_DATA_TOKEN, token_params).fetchone()

    if not params:
        return 'Invalid token. Link might have expired.', 401

    with api_tx('read committed') as tx:
        tx.execute('SET LOCAL statement_timeout = 30000') # 30 seconds
        raw_data = tx.execute(Q_EXPORT_API_DATA, params).fetchone()['j']

    person_id = params['person_id']

    inferred_personality_data = get_me(person_id_as_int=person_id)

    search_filters = get_search_filters_by_person_id(person_id=person_id)

    # Redact sensitive fields
    for person in raw_data['person']:
        del person['id_salt']

    # Decode messages
    for row in raw_data['mam_message'] or []:
        row['timestamp'] = datetime.fromtimestamp(
            timestamp=(row['id'] >> 8) / 1_000_000,
            tz=timezone.utc,
        ).isoformat()

        # this is a json string that looks like: \x836804640005786d6c656c6d00000
        message = row['message']

        # Remove the \x prefix
        no_prefix = message[2:]

        # Bytes object
        json_decoded = bytes.fromhex(no_prefix)

        erlang_decoded = erlastic.decode(json_decoded)

        row['message'] = json.dumps(erlang_decoded, cls=BytesEncoder)

    # Return the result
    exported_dict = dict(
        raw_data=raw_data,
        inferred_personality_data=inferred_personality_data,
        search_filters=search_filters,
    )

    exported_string = json.dumps(exported_dict, indent=2)

    exported_bytes = exported_string.encode()

    exported_bytesio = io.BytesIO(exported_bytes)

    return send_file(
        exported_bytesio,
        mimetype='text/json',
        as_attachment=True,
        download_name='export.json',
    )

def post_revenuecat(req: t.PostRevenuecat):
    def get_has_gold() -> Tuple[list[str], list[str]]:
        match req.event:
            case t.InitialPurchaseEvent(app_user_id=app_user_id):
                return [], [app_user_id]
            case t.RenewalEvent(app_user_id=app_user_id):
                return [], [app_user_id]
            case t.ExpirationEvent(app_user_id=app_user_id):
                return [app_user_id], []
            case t.TransferEvent(
                    transferred_to=transferred_to,
                    transferred_from=transferred_from):
                return transferred_from, transferred_to

        return [], []


    def get_has_gold_params_seq():
        has_no_gold_uuids, has_gold_uuids = get_has_gold()

        has_no_gold_params_seq = [
            dict(
                person_uuid=person_uuid,
                has_gold=False,
            )
            for person_uuid in has_no_gold_uuids
        ]

        has_gold_params_seq = [
            dict(
                person_uuid=person_uuid,
                has_gold=True
            )
            for person_uuid in has_gold_uuids
        ]

        return (
            has_no_gold_params_seq +
            has_gold_params_seq)


    try:
        auth_header = request.headers.get("Authorization", "")
        bearer, revenuecat_token = auth_header.split()
        if bearer.lower() != 'bearer':
            raise Exception()
    except:
        return 'Missing or malformed authorization header', 400

    has_gold_params_seq = get_has_gold_params_seq()

    with api_tx() as tx:
        tx.execute(
            Q_SELECT_REVENUECAT_AUTHORIZED,
            dict(token_hash_revenuecat=sha512(revenuecat_token)),
        )
        if not tx.fetchone():
            return 'Unauthorized', 401

        if not has_gold_params_seq:
            return 'Payload ignored because of its format', 200

        tx.executemany(
            Q_UPDATE_GOLD_FROM_REVENUECAT,
            has_gold_params_seq,
            returning=True
        )

        all_uuids = set(str(x['person_uuid']) for x in has_gold_params_seq)
        updated_uuids = set(str(x['person_uuid']) for x in fetchall_sets(tx))
        ignored_uuids = all_uuids - updated_uuids

        return dict(
            all_uuids=sorted(all_uuids),
            updated_uuids=sorted(updated_uuids),
            ignored_uuids=sorted(ignored_uuids),
        )

def get_visitors(s: t.SessionInfo):
    with api_tx('READ COMMITTED') as tx:
        tx.execute(Q_VISITORS, dict(person_id=s.person_id))
        return tx.fetchone()['j']

def post_mark_visitors_checked(
    req: t.PostMarkVisitorsChecked,
    s: t.SessionInfo
):
    params = dict(
        person_id=s.person_id,
        when=req.time,
    )
    with api_tx('READ COMMITTED') as tx:
        tx.execute(Q_MARK_VISITORS_CHECKED, params)
