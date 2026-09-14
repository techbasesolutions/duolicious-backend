"""Fail-closed dispatch check (Wave 1 remediation, F02).

The publish worker's very last check before it posts a card to Facebook or
Instagram. `GET /admin/growth/queue/<id>/eligible` delegates to this function
so the answer the worker acts on and the answer this route reports can never
drift apart. Every condition below is checked explicitly; the first failure
wins, and any row this function has not walked all the way to the end
answers False -- there is no path that defaults to True.

Bound to the lease `claim_spotlight_posts` hands out (spec: only the worker
holding the current lease may dispatch a row), to Task 2's immutable
revision (the exact caption/photo/participants a member consented to, not
whatever the mutable queue row now says), and to the two Wave 1 kill
switches. Every function here runs inside the caller's api_tx; none opens
one.
"""
from __future__ import annotations

from typing import Optional

from service.spotlight.eligibility import eligibility
from service.spotlight.queue import settings
from service.spotlight.revisions import consent_complete

REASONS = ('not_found', 'not_processing', 'lease_required', 'lease_mismatch', 'lease_expired', 'withdrawn',
           'no_revision', 'not_rendered', 'consent_incomplete', 'subject_missing', 'subject:<reason>',
           'participant:<person_id>:<reason>', 'publication_disabled', 'external_access_disabled')

_Q_ROW = """
    SELECT status, lease_token, (lease_until IS NOT NULL AND lease_until > NOW()) AS lease_valid,
           cancellation_requested_at, current_revision_id, kind, subject_person_id, request_key
      FROM publishing_queue WHERE id = %(id)s
"""

_Q_REVISION = """
    SELECT asset_hash, photo_uuid::text AS photo_uuid, participants
      FROM spotlight_revision WHERE id = %(id)s
"""


def dispatch_check(tx, queue_id, lease_token: Optional[str]) -> tuple[bool, str]:
    row = tx.execute(_Q_ROW, dict(id=queue_id)).fetchone()
    if not row:
        return False, 'not_found'
    if row['status'] != 'processing':
        return False, 'not_processing'
    if not lease_token:
        return False, 'lease_required'
    if lease_token != row['lease_token']:
        return False, 'lease_mismatch'
    if not row['lease_valid']:
        return False, 'lease_expired'
    if row['cancellation_requested_at'] is not None:
        return False, 'withdrawn'
    if row['current_revision_id'] is None:
        return False, 'no_revision'
    rev = tx.execute(_Q_REVISION, dict(id=row['current_revision_id'])).fetchone()
    if not rev or rev['asset_hash'] is None:
        return False, 'not_rendered'
    if not consent_complete(tx, row['current_revision_id']):
        return False, 'consent_incomplete'
    if row['kind'] != 'roundup':
        if row['subject_person_id'] is None:
            return False, 'subject_missing'
        ok, reason = eligibility(tx, row['subject_person_id'], rev['photo_uuid'],
                                 exclude_request_key=row['request_key'])
        if not ok:
            return False, f'subject:{reason}'
    else:
        # Count-only (no participants stored) is fine by design; each named
        # participant is re-checked here rather than trusted from creation
        # time, since days can pass between the snapshot and the publish.
        for participant in (rev['participants'] or []):
            person_id = participant.get('person_id')
            ok, reason = eligibility(tx, person_id, participant.get('photo_uuid'),
                                     exclude_request_key=row['request_key'])
            if not ok:
                return False, f'participant:{person_id}:{reason}'
    cfg = settings(tx)
    if cfg.get('publication_enabled') != 'true':
        return False, 'publication_disabled'
    if cfg.get('external_access_enabled') != 'true':
        return False, 'external_access_disabled'
    return True, ''
