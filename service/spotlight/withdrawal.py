"""The single Spotlight withdrawal operation (Wave 1 remediation, F03).

The adversarial review found that account deletion, admin delete, bans and
the pending-deletion cron never touched Spotlight, so a departing member's
card could stay queued or published after the member themselves was gone.
`withdraw_member` is the one function every lifecycle exit calls -- opt-out,
self-delete, admin deactivate/hard-delete, admin ban, the pending-deletion
cron, and (were one to exist) a moderation action -- so there is exactly one
place that has to get this right.

Runs entirely inside the caller's transaction; never opens one. Callers on
the async side (the pending-deletion cron) run it on a thread via a
separate SYNC `database.api_tx()`, following the pattern
`service.cron.spotlightretention` already uses for sync DB work from an
async cron -- see `service/cron/pendingdeletion/__init__.py::_withdraw_all`.
"""
from __future__ import annotations

from typing import Optional

from service.spotlight.revisions import create_revision, current_revision
from service.spotlight.storage import delete_images

REASONS = ('opt_out', 'account_deletion', 'admin_delete', 'ban', 'hard_delete', 'moderation')

# Containment against the stored tile snapshot: a tile object carries
# first_name and photo_url as well, so `@>` with just the person_id matches
# the whole tile without having to reproduce the rest of it. Moved here from
# service.spotlight.queue (Wave 1 F03): cancel_for_member there is now a
# thin wrapper over withdraw_member and no longer needs it directly.
_TILE_MATCH = """
    kind = 'roundup'
      AND payload->'tiles' @> jsonb_build_array(jsonb_build_object('person_id', %(pid)s))
"""

# A row also belongs to the member if a roundup's CURRENT revision lists
# them as a participant (Task 2's revision-bound participants, distinct
# from the legacy payload->'tiles' snapshot the line above matches). Both
# mechanisms can name a member in a live roundup, so removal, the
# mid-publish stamp and cancellation all have to check both.
_TILE_OR_PARTICIPANT_MATCH = f"""(
    ({_TILE_MATCH})
    OR EXISTS (SELECT 1 FROM spotlight_revision sr WHERE sr.id = publishing_queue.current_revision_id
                 AND sr.participants @> jsonb_build_array(jsonb_build_object('person_id', %(pid)s)))
)"""

# The member is party to a row as its subject, a roundup tile, or a
# revision participant. Shared by every step below so "subject, tile or
# participant" means the same thing everywhere.
_MEMBER_MATCH = f"(subject_person_id = %(pid)s OR {_TILE_OR_PARTICIPANT_MATCH})"

_Q_PUBLISHED_ROWS = f"""
    SELECT id, platform, external_post_id, request_key FROM publishing_queue
     WHERE status = 'published' AND {_MEMBER_MATCH}
"""

_Q_STAMP_PUBLISHED = f"""
    UPDATE publishing_queue SET cancellation_requested_at = COALESCE(cancellation_requested_at, NOW())
     WHERE status = 'published' AND {_MEMBER_MATCH}
"""

_Q_STAMP_PROCESSING = f"""
    UPDATE publishing_queue SET cancellation_requested_at = NOW()
     WHERE status = 'processing' AND cancellation_requested_at IS NULL AND {_MEMBER_MATCH}
"""

# Roundup rows short of publish, naming the member as a revision participant.
# 'scheduled' is deliberately excluded (ruling recorded in the ledger): a
# scheduled row already has a rendered asset that shows the member, and
# re-rendering it is Wave 3 work -- it is cancelled in step 5 instead.
_Q_REISSUE_CANDIDATES = """
    SELECT DISTINCT q.request_key
      FROM publishing_queue q
      JOIN spotlight_revision r ON r.id = q.current_revision_id
     WHERE q.kind = 'roundup'
       AND q.status IN ('awaiting_render', 'review', 'failed')
       AND r.participants @> jsonb_build_array(jsonb_build_object('person_id', %(pid)s))
"""

_Q_CANCEL_IMAGE_KEYS = f"""
    SELECT image_key FROM publishing_queue
     WHERE status NOT IN ('published', 'cancelled', 'processing')
       AND image_key IS NOT NULL
       AND NOT (request_key = ANY(%(reissued)s::text[]))
       AND {_MEMBER_MATCH}
"""

# A subject row's own card just carries the reason; a tile/participant row
# (the member shown in someone ELSE's roundup) keeps the existing
# tile_member_<reason> string the admin worker and removals list already
# recognise -- the caller supplies the right one via the `reason` param.
_Q_CANCEL_SUBJECT_ROWS = """
    UPDATE publishing_queue SET status = 'cancelled', error = %(reason)s,
           cancellation_requested_at = COALESCE(cancellation_requested_at, NOW()), updated_at = NOW()
     WHERE status NOT IN ('published', 'cancelled', 'processing')
       AND NOT (request_key = ANY(%(reissued)s::text[]))
       AND subject_person_id = %(pid)s
"""

_Q_CANCEL_TILE_OR_PARTICIPANT_ROWS = f"""
    UPDATE publishing_queue SET status = 'cancelled', error = %(reason)s,
           cancellation_requested_at = COALESCE(cancellation_requested_at, NOW()), updated_at = NOW()
     WHERE status NOT IN ('published', 'cancelled', 'processing')
       AND NOT (request_key = ANY(%(reissued)s::text[]))
       AND {_TILE_OR_PARTICIPANT_MATCH}
"""


def _file_removal_tasks(tx, rows, person_id: Optional[int]) -> int:
    """One open task per published platform row. A Facebook post can be
    deleted through the Graph API; Instagram has no delete endpoint for
    published media, so that one is flagged for a human instead.

    Those two reason strings are the contract the admin worker and the
    removals list read, so a task filed for a roundup tile/participant uses
    exactly the same pair as one filed for a card's subject. Nothing
    downstream has to know why the task exists in order to action it.

    `person_id` and `request_key` are snapshotted onto the task row itself
    (Task 1's migration 0044 columns): the task must still name who and
    what it is for after the member's own person row -- and possibly the
    queue row's subject_person_id -- is gone.

    Guarded with NOT EXISTS on an already-open task for the same queue_id:
    a roundup's tile/participant list can name several members, so more
    than one of them withdrawing matches the same published queue rows,
    and without this guard each withdrawal would file its own duplicate
    task for the same post."""
    n = 0
    for r in rows:
        cur = tx.execute(
            """INSERT INTO spotlight_removal_task (queue_id, platform, external_post_id, reason, person_id, request_key)
               SELECT %(q)s, %(pl)s, %(ext)s, %(reason)s, %(pid)s, %(rk)s
                WHERE NOT EXISTS (SELECT 1 FROM spotlight_removal_task t
                                   WHERE t.queue_id = %(q)s AND t.done_at IS NULL)""",
            dict(q=r['id'], pl=r['platform'], ext=r['external_post_id'], pid=person_id, rk=r['request_key'],
                 reason='delete_via_api' if r['platform'] == 'facebook' else 'manual_instagram'))
        n += cur.rowcount
    return n


def withdraw_member(tx, person_id: int, reason: str) -> dict:
    """The single exit path every lifecycle event (opt-out, self-delete,
    admin deactivate, admin hard-delete, admin ban, the pending-deletion
    cron, and a moderation action, if one existed) calls before the member
    disappears or their consent is withdrawn.

    Idempotent: a second call for the same person changes nothing and
    returns zeros for every count, EXCEPT `epoch`, which always advances --
    a withdrawal is a consent event even when nothing was queued, so that
    one field is deliberately excluded from the idempotence claim.

    Returns dict(cancelled=int, left_attempting=int, roundups_reissued=int,
                 removal_tasks=int, nonces_invalidated=int, epoch=int)."""
    if reason not in REASONS:
        raise ValueError('bad_reason')
    if not tx.execute("SELECT 1 FROM person WHERE id = %(pid)s", dict(pid=person_id)).fetchone():
        raise ValueError('not_found')
    params = dict(pid=person_id)

    # Step 2: published rows naming the member (subject, tile or
    # participant) get a removal task filed and are stamped with
    # cancellation_requested_at, but keep their 'published' status -- that
    # is still the truth until the platform post is actually gone, and the
    # task (or the retention sweep) is what records that it has to go.
    published_rows = tx.execute(_Q_PUBLISHED_ROWS, params).fetchall()
    removal_tasks = _file_removal_tasks(tx, published_rows, person_id)
    tx.execute(_Q_STAMP_PUBLISHED, params)

    # Step 3: a row mid-publish is left to finish -- only the cancellation
    # stamp is set here. The lease holder completes the attempt; a late
    # receipt against a stamped row is what files the removal task
    # (Task 5), not this function.
    left_attempting = tx.execute(_Q_STAMP_PROCESSING, params).rowcount

    # Step 4: a roundup short of publish is re-issued as a fresh revision
    # with the member dropped, rather than cancelled outright, so the other
    # participants' card can still go out.
    reissue_keys = [r['request_key'] for r in tx.execute(_Q_REISSUE_CANDIDATES, params).fetchall()]
    for request_key in reissue_keys:
        rev = current_revision(tx, request_key)
        # Scoped to the same three reissuable statuses as the candidate
        # query above: a published or cancelled sibling row of the SAME
        # request_key (e.g. one platform already posted before the other
        # was withdrawn) must keep its own status, external_post_id and
        # image_key untouched -- it was already handled by step 2's
        # removal-task filing if published, or is simply done if cancelled.
        old_keys = [r['image_key'] for r in tx.execute(
            """SELECT image_key FROM publishing_queue
                WHERE request_key = %(rk)s AND status IN ('awaiting_render', 'review', 'failed')
                  AND image_key IS NOT NULL""",
            dict(rk=request_key)).fetchall()]
        create_revision(
            tx, request_key, caption=rev['caption'], photo_uuid=None,
            participants=[p for p in (rev['participants'] or []) if p['person_id'] != person_id],
            channels=rev['channels'], layout_version=rev['layout_version'],
            created_by=f'withdrawal:{reason}', ignore_terminal_siblings=True)
        # The new revision has no render of its own yet, so every REISSUABLE
        # row of the request_key goes back to awaiting_render -- a direct
        # status write (not set_status/TRANSITIONS), the same way
        # expire_member_approvals bypasses it for a system-driven sweep
        # rather than a single queue-row action.
        tx.execute(
            """UPDATE publishing_queue SET status = 'awaiting_render', image_key = NULL, image_url = NULL,
                      updated_at = NOW()
                WHERE request_key = %(rk)s AND status IN ('awaiting_render', 'review', 'failed')""",
            dict(rk=request_key))
        delete_images(old_keys)
    roundups_reissued = len(reissue_keys)

    # Step 5: everything else naming the member -- not published, not
    # already cancelled, not mid-publish, and not just re-issued above --
    # is cancelled outright. Collect image keys before the UPDATEs blank
    # them; storage deletion is best-effort and must never affect whether
    # the cancellation itself succeeds.
    cancel_params = dict(params, reissued=reissue_keys)
    image_keys = [r['image_key'] for r in tx.execute(_Q_CANCEL_IMAGE_KEYS, cancel_params).fetchall()]
    cancelled = tx.execute(_Q_CANCEL_SUBJECT_ROWS, dict(cancel_params, reason=reason)).rowcount
    cancelled += tx.execute(_Q_CANCEL_TILE_OR_PARTICIPANT_ROWS,
                            dict(cancel_params, reason=f'tile_member_{reason}')).rowcount
    delete_images(image_keys)

    # Step 6: always bumps, whether or not anything above changed a row --
    # a withdrawal is itself a consent event.
    epoch = tx.execute(
        """UPDATE person SET spotlight_consent_epoch = spotlight_consent_epoch + 1
            WHERE id = %(pid)s RETURNING spotlight_consent_epoch""",
        params).fetchone()['spotlight_consent_epoch']

    # Step 7: any nonce still live for this member (a card link or a
    # confirm link) is burned so it cannot be replayed after the withdrawal.
    nonces_invalidated = tx.execute(
        "UPDATE spotlight_token_nonce SET used_at = NOW() WHERE person_id = %(pid)s AND used_at IS NULL",
        params).rowcount

    return dict(cancelled=cancelled, left_attempting=left_attempting, roundups_reissued=roundups_reissued,
                removal_tasks=removal_tasks, nonces_invalidated=nonces_invalidated, epoch=epoch)
