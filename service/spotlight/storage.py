"""Spaces object deletion for spotlight card images (spec 3.1, 3.6).

Deletion is always best-effort: a storage-side failure must never block a
cancellation, a retention sweep, a removal being marked done, or (Wave 1
F03) a member withdrawal. Every failure is printed, never raised -- and,
per `_bucket`'s bounded connect/read timeouts and `_configured`'s
unconfigured-environment no-op below, "failure" can never mean "blocks
forever" either.
"""
from __future__ import annotations


def _configured() -> bool:
    """False when the object-store credentials or bucket name are blank --
    `.env.production.template` ships them blank until a deployment is given
    real Spaces/R2 values, and a test environment can point them at a mock
    endpoint that simply isn't running. `delete_images` skips the network
    call entirely in that case rather than attempting (and, per `_bucket`'s
    timeouts, eventually failing) a call against nothing."""
    from service.person import R2_ACCESS_KEY_ID, R2_BUCKET_NAME
    return bool(R2_ACCESS_KEY_ID) and bool(R2_BUCKET_NAME)


def _bucket():
    """The same credentials and endpoint `service/api/admin/spotlight_routes.py`
    uploads spotlight cards through (`service.person`'s R2/Spaces settings),
    but its own client -- bounded with a short connect timeout, a longer
    read timeout, and no retries, so an unreachable or slow endpoint fails
    fast instead of blocking whichever transaction called `delete_images`
    (a withdrawal, a cancellation, a retention sweep) potentially for
    minutes. Resolved lazily (imported here, not at module scope) so tests
    can monkeypatch this function directly."""
    import boto3
    from botocore.config import Config
    from service.person import R2_ACCESS_KEY_ID, R2_ACCESS_KEY_SECRET, R2_BUCKET_NAME, BOTO_ENDPOINT_URL
    s3 = boto3.resource(
        's3',
        endpoint_url=BOTO_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_ACCESS_KEY_SECRET,
        config=Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 1}),
    )
    return s3.Bucket(R2_BUCKET_NAME)


# S3 (and Spaces) reject a DeleteObjects request carrying more than 1000 keys,
# so a large sweep is sent in chunks rather than one oversized call that would
# fail whole. A failing chunk is logged and the rest still go.
BATCH_SIZE = 1000


def delete_images(keys: list[str]) -> int:
    """Delete each key from the object store. Returns the count requested
    (not the count actually confirmed deleted) regardless of outcome, since
    a missing object or a storage error must never raise here. Returns 0
    without attempting a network call at all when the object store is not
    configured (`_configured`).

    Called from inside the caller's transaction today (see
    `service.spotlight.withdrawal.withdraw_member` and the retention sweep).
    That is deliberate for now and a known follow-up: the right place is
    after the commit, so a rolled back cancellation cannot leave the object
    already deleted. It is safe in the meantime because deletion never
    raises (and, per `_bucket`'s bounded timeouts, never hangs) and the rows
    it deletes for are ones no live post points at."""
    if not keys:
        return 0
    if not _configured():
        print('spotlight.storage.delete_images: object store not configured, skipping')
        return 0
    for start in range(0, len(keys), BATCH_SIZE):
        batch = keys[start:start + BATCH_SIZE]
        try:
            response = _bucket().delete_objects(
                Delete={'Objects': [{'Key': k} for k in batch], 'Quiet': True})
        except Exception as e:
            print(f'spotlight.storage.delete_images: failed to delete {len(batch)} object(s): {e!r}')
            continue
        for err in (response or {}).get('Errors') or []:
            print(f"spotlight.storage.delete_images: error deleting {err.get('Key')}: {err.get('Message')}")
    return len(keys)
