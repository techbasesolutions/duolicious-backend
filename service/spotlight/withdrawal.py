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

from service.spotlight.revisions import TERMINAL, create_revision, current_revision
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

# A row is still in flight while nobody can say what the platform did with
# it: `processing` under a live lease, or parked in `review` with its
# delivery unresolved (a reaped lease leaves delivery_state 'attempting'; a
# `delivery_unknown` receipt leaves it that). Cancelling either would orphan
# a post that may already be live, since nothing downstream would ever file a
# removal task for it -- so both are stamped and left where they are, and the
# `review` ones get an `investigate` task a human can act on.
_IN_FLIGHT_MATCH = """(
    status = 'processing'
    OR (status = 'review' AND delivery_state IN ('attempting', 'delivery_unknown'))
)"""

_Q_STAMP_IN_FLIGHT = f"""
    UPDATE publishing_queue SET cancellation_requested_at = NOW()
     WHERE {_IN_FLIGHT_MATCH} AND cancellation_requested_at IS NULL AND {_MEMBER_MATCH}
 RETURNING id, platform, status, request_key
"""

# Not cancellable: already done (the TERMINAL tuple, imported from revisions
# rather than respelled here), or still in flight per the match above.
_NOT_CANCELLABLE = f"(status = ANY(%(terminal)s::text[]) OR {_IN_FLIGHT_MATCH})"

# The three statuses a roundup can be re-issued from, minus any row whose
# delivery is unresolved: re-issuing one of those would blank the image_key
# and delete the artwork of a card that may be live. `{q}` is the table
# alias the embedding query uses (empty, or 'q.').
_REISSUABLE = """{q}status IN ('awaiting_render', 'review', 'failed')
       AND {q}delivery_state NOT IN ('attempting', 'delivery_unknown')"""

# Roundup rows short of publish, naming the member as a revision participant.
# 'scheduled' is deliberately excluded (ruling recorded in the ledger): a
# scheduled row already has a rendered asset that shows the member, and
# re-rendering it is Wave 3 work -- it is cancelled in step 5 instead.
_Q_REISSUE_CANDIDATES = f"""
    SELECT DISTINCT q.request_key
      FROM publishing_queue q
      JOIN spotlight_revision r ON r.id = q.current_revision_id
     WHERE q.kind = 'roundup'
       AND {_REISSUABLE.format(q='q.')}
       AND r.participants @> jsonb_build_array(jsonb_build_object('person_id', %(pid)s))
"""

_Q_CANCEL_IMAGE_KEYS = f"""
    SELECT image_key FROM publishing_queue
     WHERE NOT {_NOT_CANCELLABLE}
       AND image_key IS NOT NULL
       AND NOT (request_key = ANY(%(reissued)s::text[]))
       AND {_MEMBER_MATCH}
"""

# A subject row's own card just carries the reason; a tile/participant row
# (the member shown in someone ELSE's roundup) keeps the existing
# tile_member_<reason> string the admin worker and removals list already
# recognise -- the caller supplies the right one via the `reason` param.
_Q_CANCEL_SUBJECT_ROWS = f"""
    UPDATE publishing_queue SET status = 'cancelled', error = %(reason)s,
           cancellation_requested_at = COALESCE(cancellation_requested_at, NOW()), updated_at = NOW()
     WHERE NOT {_NOT_CANCELLABLE}
       AND NOT (request_key = ANY(%(reissued)s::text[]))
       AND subject_person_id = %(pid)s
"""

_Q_CANCEL_TILE_OR_PARTICIPANT_ROWS = f"""
    UPDATE publishing_queue SET status = 'cancelled', error = %(reason)s,
           cancellation_requested_at = COALESCE(cancellation_requested_at, NOW()), updated_at = NOW()
     WHERE NOT {_NOT_CANCELLABLE}
       AND NOT (request_key = ANY(%(reissued)s::text[]))
       AND {_TILE_OR_PARTICIPANT_MATCH}
"""


def _file_removal_tasks(tx, rows, person_id: Optional[int], reason: Optional[str] = None) -> int:
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
    task for the same post.

    `reason` overrides the platform-derived pair for a task that is not a
    deletion order at all: an `investigate` task (fix wave item 1) asks a
    human to find out whether a post exists before anything is deleted.
    The admin removals worker actions only `delete_via_api` and leaves every
    other reason alone, so an override is safe on both platforms."""
    n = 0
    for r in rows:
        task_reason = reason or ('delete_via_api' if r['platform'] == 'facebook' else 'manual_instagram')
        # An open `investigate` task is a placeholder: it asks a human to
        # find out whether a post exists. Once that is answered -- the row
        # reaches `published` through a late receipt or an operator's
        # reconcile -- the placeholder becomes the real removal order in
        # place, carrying the external id it now has, rather than sitting
        # open beside it. Never the other way round: a real order is never
        # downgraded back to `investigate`.
        upgraded = tx.execute(
            """UPDATE spotlight_removal_task
                   SET reason = %(reason)s, external_post_id = %(ext)s
                 WHERE queue_id = %(q)s AND done_at IS NULL AND reason = 'investigate'
                   AND %(reason)s <> 'investigate'""",
            dict(q=r['id'], ext=r['external_post_id'], reason=task_reason)).rowcount
        cur = tx.execute(
            """INSERT INTO spotlight_removal_task (queue_id, platform, external_post_id, reason, person_id, request_key)
               SELECT %(q)s, %(pl)s, %(ext)s, %(reason)s, %(pid)s, %(rk)s
                WHERE NOT EXISTS (SELECT 1 FROM spotlight_removal_task t
                                   WHERE t.queue_id = %(q)s AND t.done_at IS NULL)""",
            dict(q=r['id'], pl=r['platform'], ext=r['external_post_id'], pid=person_id, rk=r['request_key'],
                 reason=task_reason))
        n += cur.rowcount + upgraded
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

    # Step 3: a row still in flight is left where it is -- only the
    # cancellation stamp is set here. A `processing` row's lease holder
    # completes the attempt, and a late receipt against the stamped row is
    # what files its removal task (Task 5), not this function. A `review`
    # row whose delivery was never resolved has no lease holder left to come
    # back, so it gets an `investigate` task straight away: an operator has
    # to look at the page and either reconcile the row (which files the real
    # removal task) or confirm nothing was ever posted. Its external_post_id
    # is NULL by definition -- the row never reached `published`.
    in_flight = tx.execute(_Q_STAMP_IN_FLIGHT, params).fetchall()
    left_attempting = len(in_flight)
    removal_tasks += _file_removal_tasks(
        tx,
        [dict(id=r['id'], platform=r['platform'], external_post_id=None, request_key=r['request_key'])
         for r in in_flight if r['status'] == 'review'],
        person_id, reason='investigate')

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
            f"""SELECT image_key FROM publishing_queue
                WHERE request_key = %(rk)s AND {_REISSUABLE.format(q='')}
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
            f"""UPDATE publishing_queue SET status = 'awaiting_render', image_key = NULL, image_url = NULL,
                      updated_at = NOW()
                WHERE request_key = %(rk)s AND {_REISSUABLE.format(q='')}""",
            dict(rk=request_key))
        # Task 5 moves this onto the cleanup job; for now the confirmed
        # count (out of `_configured`'s new list-of-confirmed-keys return)
        # is only logged, the same as `delete_images` always was best-effort.
        confirmed = delete_images(old_keys)
        print(f'spotlight.withdrawal: confirmed {len(confirmed)}/{len(old_keys)} '
              f'reissue image(s) deleted for {request_key}')
    roundups_reissued = len(reissue_keys)

    # Step 5: everything else naming the member -- not published, not
    # already cancelled, not mid-publish, and not just re-issued above --
    # is cancelled outright. Collect image keys before the UPDATEs blank
    # them; storage deletion is best-effort and must never affect whether
    # the cancellation itself succeeds.
    cancel_params = dict(params, reissued=reissue_keys, terminal=list(TERMINAL))
    image_keys = [r['image_key'] for r in tx.execute(_Q_CANCEL_IMAGE_KEYS, cancel_params).fetchall()]
    cancelled = tx.execute(_Q_CANCEL_SUBJECT_ROWS, dict(cancel_params, reason=reason)).rowcount
    cancelled += tx.execute(_Q_CANCEL_TILE_OR_PARTICIPANT_ROWS,
                            dict(cancel_params, reason=f'tile_member_{reason}')).rowcount
    # Task 5 moves this onto the cleanup job; for now only the confirmed
    # count is logged.
    confirmed = delete_images(image_keys)
    print(f'spotlight.withdrawal: confirmed {len(confirmed)}/{len(image_keys)} cancelled image(s) deleted')

    # Step 6: always bumps, whether or not anything above changed a row --
    # a withdrawal is itself a consent event.
    epoch = tx.execute(
        """UPDATE person SET spotlight_consent_epoch = spotlight_consent_epoch + 1
            WHERE id = %(pid)s RETURNING spotlight_consent_epoch""",
        params).fetchone()['spotlight_consent_epoch']

    # Step 7 (fix wave item 7): the standing preference goes with the
    # withdrawal, for every reason -- an account that is reactivated, or a
    # ban that is lifted, must not quietly resume featuring the member.
    # `spotlight_opt_in_at` is deliberately left as it was: it records when
    # they last chose to take part, and `set_spotlight_opt_in` stamps a
    # fresh one if they ever opt in again.
    tx.execute("UPDATE person SET spotlight_opt_in = FALSE WHERE id = %(pid)s", params)

    # Step 8: any nonce still live for this member (a card link or a
    # confirm link) is burned so it cannot be replayed after the withdrawal.
    nonces_invalidated = tx.execute(
        "UPDATE spotlight_token_nonce SET used_at = NOW() WHERE person_id = %(pid)s AND used_at IS NULL",
        params).rowcount

    return dict(cancelled=cancelled, left_attempting=left_attempting, roundups_reissued=roundups_reissued,
                removal_tasks=removal_tasks, nonces_invalidated=nonces_invalidated, epoch=epoch)
