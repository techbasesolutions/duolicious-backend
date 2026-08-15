"""F16: the 1-hour onboardee wipe measured row CREATION, not activity, so a
slow onboarder re-verifying an OTP lost all wizard state even though they
were actively filling it out seconds earlier. Migration 0038 adds
onboardee.updated_at; every field/photo upsert stamps it; the wipe now
reads it instead of created_at.

F17: replacing an onboarding photo at the same position kept the FIRST
upload's hash (no `hash = EXCLUDED.hash` in the upsert), so a later
photo-ban check latched onto stale image data.

F18: the selfie anti-replay hash used to latch (and the verification job
get inserted) BEFORE the object-store upload. A failed upload then burned
the capture -- the hash was permanently latched with no photo behind it.
The latch now only commits after a successful upload.
"""
from __future__ import annotations

import base64 as b64lib
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from database import api_tx


def _make_base64_image(color: tuple[int, int, int]) -> str:
    """A tiny (64x64, clears MIN_IMAGE_DIM=50) solid-color PNG as a data
    URL, suitable for duotypes.Base64File. Different colors -> different
    md5 hashes, which is what the F17 test needs to distinguish."""
    img = Image.new('RGB', (64, 64), color=color)
    buf = BytesIO()
    img.save(buf, format='PNG')
    encoded = b64lib.b64encode(buf.getvalue()).decode()
    return f'data:image/png;base64,{encoded}'


# ---------------------------------------------------------------------------
# F16 -- source-inspection guard (brief Step 2)
# ---------------------------------------------------------------------------

def test_onboardee_wipe_uses_activity_not_creation():
    from service.person.sql import Q_MAYBE_DELETE_ONBOARDEE
    assert 'updated_at' in Q_MAYBE_DELETE_ONBOARDEE, \
        'wipe window must read the activity timestamp (F16)'
    assert "created_at < NOW() - INTERVAL '1 hour'" not in Q_MAYBE_DELETE_ONBOARDEE


# ---------------------------------------------------------------------------
# F16 -- behavioral: the wipe query itself
# ---------------------------------------------------------------------------

def _seed_onboardee_with_session(email: str, otp: str, *, created_ago: str, updated_ago: str):
    session_token_hash = uuid4().hex
    with api_tx() as tx:
        tx.execute(
            f"""
            INSERT INTO onboardee (email, name, created_at, updated_at)
            VALUES (
                %(email)s, 'Freshness Test',
                NOW() - INTERVAL '{created_ago}',
                NOW() - INTERVAL '{updated_ago}'
            )
            """,
            dict(email=email),
        )
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, otp, otp_expiry)
            VALUES (%(sth)s, %(email)s, %(otp)s, NOW() + INTERVAL '10 minutes')
            """,
            dict(sth=session_token_hash, email=email, otp=otp),
        )
    return session_token_hash


def _run_wipe(session_token_hash: str, otp: str):
    from service.person.sql import Q_MAYBE_DELETE_ONBOARDEE
    with api_tx() as tx:
        tx.execute(
            Q_MAYBE_DELETE_ONBOARDEE,
            dict(session_token_hash=session_token_hash, otp=otp),
        )


def _onboardee_exists(email: str) -> bool:
    with api_tx() as tx:
        row = tx.execute(
            'SELECT 1 FROM onboardee WHERE email = %(email)s', dict(email=email)
        ).fetchone()
    return row is not None


def _cleanup(email: str):
    with api_tx() as tx:
        tx.execute('DELETE FROM duo_session WHERE email = %(email)s', dict(email=email))
        tx.execute('DELETE FROM onboardee WHERE email = %(email)s', dict(email=email))


def test_onboardee_survives_wipe_when_recently_active():
    """Row created 2 hours ago (would have been wiped under the old
    created_at window) but updated 1 minute ago -- a slow onboarder who
    just tapped 'Resend code' mid-wizard. Must survive."""
    email = f'freshness-fresh-{uuid4()}@example.com'
    otp = '123456'
    session_token_hash = _seed_onboardee_with_session(
        email, otp, created_ago='2 hours', updated_ago='1 minute')
    try:
        _run_wipe(session_token_hash, otp)
        assert _onboardee_exists(email), \
            'onboardee updated within the last hour must survive an OTP re-verify (F16)'
    finally:
        _cleanup(email)


def test_onboardee_wiped_when_stale():
    """Both created_at and updated_at are 2 hours old -- a genuinely
    abandoned attempt. Must still be wiped."""
    email = f'freshness-stale-{uuid4()}@example.com'
    otp = '654321'
    session_token_hash = _seed_onboardee_with_session(
        email, otp, created_ago='2 hours', updated_ago='2 hours')
    try:
        _run_wipe(session_token_hash, otp)
        assert not _onboardee_exists(email), \
            'a genuinely stale (no recent activity) onboardee must still be wiped'
    finally:
        _cleanup(email)


# ---------------------------------------------------------------------------
# F17 -- source-inspection guard (brief Step 2)
# ---------------------------------------------------------------------------

def test_onboardee_photo_upsert_updates_hash():
    import inspect
    import service.person as sp
    src = inspect.getsource(sp)
    marker = 'ON CONFLICT (email, position) DO UPDATE SET'
    idx = src.find(marker)
    assert idx > -1
    clause = src[idx:idx + 400]
    assert 'hash = EXCLUDED.hash' in clause, \
        'replacing an onboarding photo must update its stored hash (F17)'


# ---------------------------------------------------------------------------
# F17 -- behavioral: replacing a photo at the same position via the real
# endpoint function must change the stored hash (and, per F16, bump
# onboardee.updated_at even though onboardee_photo is a child table).
# ---------------------------------------------------------------------------

def test_onboardee_photo_replace_updates_stored_hash_and_bumps_activity(monkeypatch):
    import duotypes as t
    import service.person as sp

    # Object-store upload is irrelevant to this DB-level assertion and
    # would otherwise require a reachable bucket; stub it out.
    monkeypatch.setattr(sp, 'put_image_in_object_store', lambda *a, **k: None)

    email = f'photo-hash-{uuid4()}@example.com'
    session = SimpleNamespace(email=email)

    with api_tx() as tx:
        tx.execute(
            "INSERT INTO onboardee (email, updated_at) "
            "VALUES (%(email)s, NOW() - INTERVAL '2 hours')",
            dict(email=email),
        )

    try:
        req1 = t.PatchOnboardeeInfo(base64_file=dict(
            position=1, base64=_make_base64_image((10, 20, 30)), top=0, left=0))
        sp.patch_onboardee_info(req1, session)

        with api_tx() as tx:
            row1 = tx.execute(
                "SELECT hash FROM onboardee_photo WHERE email = %(email)s AND position = 1",
                dict(email=email),
            ).fetchone()
        assert row1 is not None
        hash1 = row1['hash']

        # Force updated_at back into the past so we can prove the SECOND
        # upsert (the replace) bumps it again.
        with api_tx() as tx:
            tx.execute(
                "UPDATE onboardee SET updated_at = NOW() - INTERVAL '2 hours' "
                "WHERE email = %(email)s",
                dict(email=email),
            )

        req2 = t.PatchOnboardeeInfo(base64_file=dict(
            position=1, base64=_make_base64_image((200, 50, 90)), top=0, left=0))
        sp.patch_onboardee_info(req2, session)

        with api_tx() as tx:
            row2 = tx.execute(
                "SELECT hash FROM onboardee_photo WHERE email = %(email)s AND position = 1",
                dict(email=email),
            ).fetchone()
            fresh = tx.execute(
                "SELECT updated_at > NOW() - INTERVAL '1 minute' AS fresh "
                "FROM onboardee WHERE email = %(email)s",
                dict(email=email),
            ).fetchone()

        assert row2['hash'] != hash1, \
            'replacing a photo at the same position must update its stored hash (F17)'
        assert fresh['fresh'], \
            'a photo replace must bump onboardee.updated_at even though ' \
            'onboardee_photo is a child table (F16)'
    finally:
        with api_tx() as tx:
            tx.execute('DELETE FROM onboardee WHERE email = %(email)s', dict(email=email))


# ---------------------------------------------------------------------------
# F18 -- behavioral: a failed object-store upload must not latch the
# anti-replay hash.
# ---------------------------------------------------------------------------

def test_selfie_upload_failure_does_not_latch_hash(monkeypatch, make_person):
    import service.person as sp

    p = make_person(name='Selfie Retry', gender='Woman')

    def _raise(*args, **kwargs):
        raise RuntimeError('object store unreachable')

    monkeypatch.setattr(sp, 'put_image_in_object_store', _raise)

    fake_hash = f'selfie-hash-{uuid4()}'
    req = SimpleNamespace(base64_file=SimpleNamespace(
        base64='', image=None, top=0, left=0, md5_hash=fake_hash))
    session = SimpleNamespace(person_id=p['id'])

    try:
        result = sp.post_verification_selfie(req, session)
        assert result == ('', 500)

        with api_tx() as tx:
            hash_row = tx.execute(
                'SELECT 1 FROM verification_photo_hash WHERE hash = %(h)s',
                dict(h=fake_hash),
            ).fetchone()
            job_row = tx.execute(
                'SELECT 1 FROM verification_job WHERE person_id = %(p)s',
                dict(p=p['id']),
            ).fetchone()

        assert hash_row is None, \
            'a failed upload must not latch the anti-replay hash (F18)'
        assert job_row is None, \
            'a failed upload must not insert a verification job either (F18)'
    finally:
        with api_tx() as tx:
            tx.execute(
                'DELETE FROM verification_photo_hash WHERE hash = %(h)s',
                dict(h=fake_hash),
            )
            tx.execute(
                'DELETE FROM verification_job WHERE person_id = %(p)s',
                dict(p=p['id']),
            )


def test_selfie_upload_success_latches_hash_and_inserts_job(monkeypatch, make_person):
    """Companion happy-path: a successful upload must still latch the
    hash and insert the job, proving the reorder didn't just delete the
    write -- it moved it."""
    import service.person as sp

    p = make_person(name='Selfie Ok', gender='Man')

    monkeypatch.setattr(sp, 'put_image_in_object_store', lambda *a, **k: None)

    fake_hash = f'selfie-hash-ok-{uuid4()}'
    req = SimpleNamespace(base64_file=SimpleNamespace(
        base64='', image=None, top=0, left=0, md5_hash=fake_hash))
    session = SimpleNamespace(person_id=p['id'])

    try:
        sp.post_verification_selfie(req, session)

        with api_tx() as tx:
            hash_row = tx.execute(
                'SELECT 1 FROM verification_photo_hash WHERE hash = %(h)s',
                dict(h=fake_hash),
            ).fetchone()
            job_row = tx.execute(
                "SELECT status FROM verification_job WHERE person_id = %(p)s",
                dict(p=p['id']),
            ).fetchone()

        assert hash_row is not None, 'a successful upload must latch the hash'
        assert job_row is not None, 'a successful upload must insert the verification job'
        assert job_row['status'] == 'uploading-photo'
    finally:
        with api_tx() as tx:
            tx.execute(
                'DELETE FROM verification_photo_hash WHERE hash = %(h)s',
                dict(h=fake_hash),
            )
            tx.execute(
                'DELETE FROM verification_job WHERE person_id = %(p)s',
                dict(p=p['id']),
            )
