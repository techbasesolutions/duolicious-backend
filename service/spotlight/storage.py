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


def delete_images(keys: list[str]) -> int:
    """Delete each key from the object store. Returns the count requested
    (not the count actually confirmed deleted) regardless of outcome, since
    a missing object or a storage error must never raise here."""
    if not keys:
        return 0
    try:
        response = _bucket().delete_objects(
            Delete={'Objects': [{'Key': k} for k in keys], 'Quiet': True})
    except Exception as e:
        print(f'spotlight.storage.delete_images: failed to delete {len(keys)} object(s): {e!r}')
        return len(keys)
    for err in (response or {}).get('Errors') or []:
        print(f"spotlight.storage.delete_images: error deleting {err.get('Key')}: {err.get('Message')}")
    return len(keys)
