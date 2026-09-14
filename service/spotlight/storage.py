"""Spaces object deletion for spotlight card images (spec 3.1, 3.6).

Deletion is always best-effort: a storage-side failure must never block a
cancellation, a retention sweep, or a removal being marked done. Every
failure is printed, never raised.
"""
from __future__ import annotations


def _bucket():
    """The same bucket resource `service/api/admin/spotlight_routes.py`
    uploads spotlight cards through, so both sides share one set of
    credentials and one endpoint override. Resolved lazily (imported here,
    not at module scope) so tests can monkeypatch this function directly."""
    from service.person import bucket
    return bucket


# S3 (and Spaces) reject a DeleteObjects request carrying more than 1000 keys,
# so a large sweep is sent in chunks rather than one oversized call that would
# fail whole. A failing chunk is logged and the rest still go.
BATCH_SIZE = 1000


def delete_images(keys: list[str]) -> int:
    """Delete each key from the object store. Returns the count requested
    (not the count actually confirmed deleted) regardless of outcome, since
    a missing object or a storage error must never raise here.

    Called from inside the caller's transaction today (see
    `service.spotlight.queue.cancel_for_member` and the retention sweep). That
    is deliberate for now and a known follow-up: the right place is after the
    commit, so a rolled back cancellation cannot leave the object already
    deleted. It is safe in the meantime because deletion never raises and the
    rows it deletes for are ones no live post points at."""
    if not keys:
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
