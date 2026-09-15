"""Durable cleanup jobs for spotlight artwork (Wave 2 F09 part 2).

Before this module, every place that finished with a rendered card deleted
the object inline and cleared `publishing_queue.image_key` in the same
breath: the retention sweep, the removal-done route, a member withdrawal's
cancelled rows, a superseded upload. Two things were wrong with that. The
delete ran INSIDE the caller's transaction, so a network round trip held the
api connection lock and a rolled back cancellation could still have deleted
the object. And the key was cleared whether or not the object actually went
away, so an unconfigured store, a credentials error or a transient Spaces
failure left artwork in the bucket that nothing in the database named any
more -- unreachable, unbilled for by anyone paying attention, and impossible
to retry.

The job table splits that into two halves that fail independently:

  * ENQUEUE writes one `cleanup_job` row, in the SAME transaction as whatever
    decided the artwork is finished with. It is a plain database write, so it
    belongs inside the caller's transaction and never blocks on anything. The
    key stays on the queue row.

  * `run_cleanup_batch` is the ONLY place storage deletion happens. One
    transaction reserves what is due, the delete call runs with NO transaction
    open, and a second transaction records each result -- and clears the image
    columns of every row naming the key, on `publishing_queue` AND on
    `spotlight_revision` (fix wave item 4), only for the keys storage actually
    CONFIRMED.

So a key is never cleared until its deletion is confirmed, and a failure is a
retry with backoff rather than a silent orphan. A job that exhausts
`MAX_ATTEMPTS` is abandoned with an alert line and LEFT in the table: nothing
sweeps it up, because at that point a human needs to look at the bucket.

KEYS COME BACK (fix round 1). Spotlight keys are content-hashed
(`assets.asset_key`), so re-uploading identical bytes produces the identical
key. Two consequences, and both are handled by recording WHO named the key at
enqueue time:

  * Uniqueness is over PENDING jobs only (migration 0047's partial index).
    A done, failed or abandoned job stays as the record of what happened and
    never blocks the key's next lifetime from being queued.

  * `enqueue_asset_delete` snapshots the rows that name the key right now
    into `evidence.owners`, and the batch re-checks every reserved job before
    deleting. A reference that is NOT in that snapshot means the key came back
    to life between the enqueue and the batch, so the object is left alone,
    the queue row is not cleared, and the job is filed done with
    `evidence.referenced`. The snapshot is what lets the retained-key rule and
    this guard coexist: retention, a removal marked done and a withdrawal all
    queue a key that is deliberately still ON its row, and that reference is
    the job's own, not a new one.

`settings` is imported lazily inside `run_cleanup_batch`: `service.spotlight.
queue` imports `service.spotlight.revisions` at load time and `revisions`
imports `enqueue_asset_delete` from here, so a top-level import back to
`queue` would be a cycle. Same reasoning (and same shape) as `approve_card`'s
lazy import in `revisions.py`.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from service.spotlight.storage import delete_images

# Backoff applied AFTER attempt n fails, indexed by n - 1; attempts past the
# end of the tuple all wait a day. Longer than the email outbox's equivalent
# on purpose: nobody is waiting on a deleted object, and the usual cause of a
# failure here is an object store outage or a bad credential, neither of which
# is fixed by trying again in a minute.
BACKOFF_SECONDS = (60, 600, 3600, 21600, 86400)

# Ten attempts spans roughly five days of backoff. A job that still cannot
# confirm by then is not a transient failure, so it stops consuming batches
# and starts asking for a human instead.
MAX_ATTEMPTS = 10

# One batch reserves at most this many jobs. `delete_images` chunks its own
# requests at 1000 keys, so this is about bounding how long a single tick
# holds the storage call, not about the API's limits.
BATCH = 200

# Every row that can name a stored object. A key is safe to delete only when
# nothing here points at it except the owner recorded when the job was queued.
# `spotlight_revision.image_key` matters as much as the queue row's: a
# revision is what an admin view and a member's preview read back.
_Q_REFERENCES = """
    SELECT image_key AS target, 'queue:' || id::text AS ref
      FROM publishing_queue
     WHERE image_key = ANY(%(k)s::text[])
    UNION ALL
    SELECT image_key AS target, 'revision:' || id::text AS ref
      FROM spotlight_revision
     WHERE image_key = ANY(%(k)s::text[])
"""

# ON CONFLICT infers migration 0047's PARTIAL unique index, so the conflict
# arm fires only against another job that is still pending. A key whose
# previous job is done can be queued again for its next lifetime.
_Q_ENQUEUE = """
    INSERT INTO cleanup_job (kind, target, evidence)
    VALUES ('asset_delete', %(t)s, %(e)s::jsonb)
    ON CONFLICT (kind, target) WHERE state = 'pending' DO NOTHING
    RETURNING id
"""

# The inner SELECT takes the row locks with SKIP LOCKED, so a second batch
# running at the same moment never blocks on -- or double-reserves -- the rows
# this one is taking. The attempts bump is the reservation: a batch that dies
# after the storage call but before recording results still leaves the attempt
# counted, which is the honest reading (the delete may well have happened).
# `%(pfx)s` is NULL in production; tests pass a per-test key prefix so a batch
# is blind to every other test's jobs and the suite is order-independent.
_Q_DUE = """
    UPDATE cleanup_job
       SET attempts = attempts + 1, updated_at = NOW()
     WHERE id IN (
        SELECT id FROM cleanup_job
         WHERE state = 'pending' AND next_attempt_at <= NOW()
           AND (%(pfx)s::text IS NULL OR target LIKE %(pfx)s)
         ORDER BY id
         LIMIT %(n)s
         FOR UPDATE SKIP LOCKED
     )
    RETURNING id, kind, target, attempts, evidence
"""

# Evidence is MERGED, not replaced: the owners snapshot written at enqueue is
# what a later reader needs to understand why a job was skipped or allowed.
_Q_DONE = """
    UPDATE cleanup_job
       SET state = 'done', done_at = NOW(), updated_at = NOW(), last_error = NULL,
           evidence = evidence || %(e)s::jsonb
     WHERE id = %(id)s
"""

_Q_ABANDON = """
    UPDATE cleanup_job SET state = 'abandoned', updated_at = NOW(), last_error = %(e)s WHERE id = %(id)s
"""

_Q_RETRY = """
    UPDATE cleanup_job
       SET next_attempt_at = NOW() + make_interval(secs => %(s)s), updated_at = NOW(), last_error = %(e)s
     WHERE id = %(id)s
"""

# The one write that clears a key, and the reason this module exists: it runs
# only for a key storage confirmed gone. image_sha256 goes with image_key
# (Task 3 fix round 1, ruling 2: a stale hash surviving a cleared key can
# complete a render set the row was never re-rendered for).
_Q_CLEAR_KEY = """
    UPDATE publishing_queue
       SET image_key = NULL, image_url = NULL, image_sha256 = NULL, updated_at = NOW()
     WHERE image_key = %(k)s
"""

# The revision names the object too, and it is the revision a member's own
# card screen reads: `approval.card_state` presigns `revision.image_key`.
# Clearing only the queue row (fix wave item 4) left the revision pointing at
# an object that is provably gone, so a member opening their card after a
# retention sweep got a signed URL for nothing. `asset_hash` is deliberately
# NOT cleared: it records that this revision was rendered, which stays true,
# and it is what the one-render-per-revision guard reads. Runs in the same
# transaction as `_Q_CLEAR_KEY`, against the same confirmed key, so the two
# tables can never disagree about whether the object exists.
_Q_CLEAR_REVISION_KEY = """
    UPDATE spotlight_revision SET image_key = NULL, image_url = NULL WHERE image_key = %(k)s
"""

_Q_OUTSTANDING = "SELECT count(*) AS n FROM cleanup_job WHERE state = 'pending'"

_Q_ABANDONED = "SELECT count(*) AS n FROM cleanup_job WHERE state = 'abandoned'"

# Task 7 (Wave 3b): the count above says something is stuck; this is what
# says WHICH object key, without an operator opening a database client to
# find out. `updated_at` (migration 0049) is the last time anything actually
# happened to the job -- a reservation bump, a retry, or the abandonment
# itself -- so "newest first" here means "most recently gave up", not merely
# "most recently created".
_Q_ABANDONED_ROWS = """
    SELECT id, kind, target, attempts, last_error, updated_at
      FROM cleanup_job
     WHERE state = 'abandoned'
     ORDER BY updated_at DESC
     LIMIT %(lim)s
"""

_Q_OVERDUE_REMOVALS = """
    SELECT count(*) AS n FROM spotlight_removal_task
     WHERE done_at IS NULL AND deadline_at < NOW()
"""


def _references(tx, keys: list) -> dict:
    """Every row currently naming each key, as {key: {'queue:<id>', ...}}.
    Keys nothing names are absent rather than mapped to an empty set."""
    out: dict = {}
    if not keys:
        return out
    for r in tx.execute(_Q_REFERENCES, dict(k=list(keys))).fetchall():
        out.setdefault(r['target'], set()).add(r['ref'])
    return out


def is_referenced(tx, key: str) -> bool:
    """True when any queue row or revision still names this object. Used by
    the image route's superseded path, which must not queue an orphan-cleanup
    for a key that a live row happens to share (identical bytes, identical
    content-hashed key)."""
    return bool(key) and bool(_references(tx, [key]).get(key))


def enqueue_asset_delete(tx, key: str) -> Optional[int]:
    """Record that one stored object is finished with. Runs inside the
    caller's transaction: this is a database write, not an outbound call, so
    it commits or rolls back with whatever decided the artwork is done.

    The rows naming the key RIGHT NOW are snapshotted into `evidence.owners`.
    Those are the references this job is entitled to retire -- the retained
    key on a swept, cancelled or removed row is the normal case, not a reason
    to refuse. Anything naming the key later is a new lifetime and stops the
    batch (see the module docstring).

    Returns the new job id, or None when a job for this key is already
    PENDING (migration 0047's partial unique index is the idempotency point,
    so a retried request, a re-run sweep or two producers naming the same key
    add nothing, while a key whose previous job is finished can be queued
    again). A blank key is a no-op rather than a row: nothing to delete."""
    if not key:
        return None
    owners = sorted(_references(tx, [key]).get(key, set()))
    row = tx.execute(_Q_ENQUEUE, dict(t=key, e=json.dumps(dict(owners=owners)))).fetchone()
    return row['id'] if row else None


def due_jobs(tx, limit: int = BATCH, target_prefix: Optional[str] = None) -> list[dict]:
    """Reserve up to `limit` pending jobs whose next attempt has come round,
    oldest first, bumping each one's attempt count. Returns the reserved rows
    (id, kind, target, attempts, evidence).

    `target_prefix` restricts the reservation to keys under one prefix.
    Production passes None (one cron, one drain, the whole table); tests pass
    their own prefix so a batch never consumes another test's jobs."""
    pattern = None if target_prefix is None else target_prefix + '%'
    return tx.execute(_Q_DUE, dict(n=limit, pfx=pattern)).fetchall()


def record_result(tx, job_id, *, confirmed: bool, error: Optional[str],
                   evidence: Optional[dict] = None) -> str:
    """File one job's outcome. Returns 'done', 'pending' (requeued with
    backoff), 'abandoned', or 'missing' when the row is no longer there.

    'missing' is deliberately not folded into 'done' (fix round 1, ruling 4):
    a job that vanished between its reservation and this write was not a
    confirmed deletion, and counting it as one would overstate what the batch
    achieved.

    `attempts` is read back rather than passed in because `due_jobs` already
    bumped it: the number this reads is the attempt that just happened, so
    reaching MAX_ATTEMPTS here means the job has genuinely had that many
    goes. `evidence` is merged into whatever the row already carries."""
    row = tx.execute("SELECT target, attempts FROM cleanup_job WHERE id = %(id)s",
                     dict(id=job_id)).fetchone()
    if not row:
        return 'missing'
    if confirmed:
        tx.execute(_Q_DONE, dict(id=job_id, e=json.dumps(evidence or {})))
        return 'done'
    attempts = int(row['attempts'])
    if attempts >= MAX_ATTEMPTS:
        tx.execute(_Q_ABANDON, dict(e=error, id=job_id))
        # Left in the table on purpose: an abandoned job is the only record
        # that an object may still be sitting in the bucket.
        print(f"spotlight_cleanup: ABANDONED {row['target']} after {attempts} attempts")
        return 'abandoned'
    backoff = BACKOFF_SECONDS[min(max(attempts, 1), len(BACKOFF_SECONDS)) - 1]
    tx.execute(_Q_RETRY, dict(s=backoff, e=error, id=job_id))
    return 'pending'


def outstanding_jobs(tx) -> int:
    """Pending jobs, whether or not they are due yet. Surfaced by the removals
    endpoint so a stop that has been engaged for a while does not hide a
    growing backlog of undeleted artwork."""
    return int(tx.execute(_Q_OUTSTANDING).fetchone()['n'])


def abandoned_jobs(tx) -> int:
    """Jobs that ran out of attempts. Nothing sweeps these up and, since fix
    wave item 3, the retention sweep no longer re-queues their keys either --
    so this count is the only thing that makes them visible. Each one is an
    object that may still be sitting in the bucket with nothing in the
    database naming it as live."""
    return int(tx.execute(_Q_ABANDONED).fetchone()['n'])


def abandoned_job_rows(tx, limit: int = 50) -> list[dict]:
    """The rows behind `abandoned_jobs`'s count, newest-abandoned first, so
    an operator can see which stored object keys are stuck without a
    database client."""
    return [dict(r) for r in tx.execute(_Q_ABANDONED_ROWS, dict(lim=limit)).fetchall()]


def overdue_removals(tx) -> int:
    """Open removal tasks past their deadline. A removal order that nobody
    actions is the failure mode that matters most here: the member asked to be
    taken down and the post is still up."""
    return int(tx.execute(_Q_OVERDUE_REMOVALS).fetchone()['n'])


def run_cleanup_batch(tx_factory, delete: Callable[[list], list] = delete_images,
                       target_prefix: Optional[str] = None) -> dict:
    """Delete one batch of due objects. Returns
    dict(reserved, done, retried, abandoned, missing, halted), plus
    `outstanding` when the emergency stop is engaged.

    Transaction shape, which is the whole point of this function: ONE
    transaction reads the stop, reserves the due jobs and re-checks their
    references; then `delete` runs with NO transaction open; then ONE
    transaction records every result and clears the queue rows for the
    confirmed keys. The delete call is a network round trip to a third party,
    and holding row locks across it is how a storage outage becomes a database
    outage.

    `external_access_enabled` is the same emergency stop every outbound call
    obeys (Task 9, F12). Deleting an object is outbound, so a stop means no
    delete call is made at all and every job stays exactly as it is -- not
    reserved, not attempted, not backed off.

    `target_prefix` is None in production; tests pass their own key prefix so
    a batch only ever sees their jobs."""
    from service.spotlight.queue import settings  # lazy: see module docstring
    empty = dict(reserved=0, done=0, retried=0, abandoned=0, missing=0)
    with tx_factory() as tx:
        if settings(tx).get('external_access_enabled') != 'true':
            return dict(empty, halted=True, outstanding=outstanding_jobs(tx))
        jobs = due_jobs(tx, target_prefix=target_prefix)
        # Re-check inside the reservation transaction, so a key that came back
        # to life is filed and committed whether or not the storage call that
        # follows ever completes.
        deletable, referenced = [], []
        current = _references(tx, [j['target'] for j in jobs])
        for job in jobs:
            owners = set(((job['evidence'] or {}).get('owners')) or [])
            if current.get(job['target'], set()) - owners:
                referenced.append(job)
            else:
                deletable.append(job)
        done = missing = 0
        for job in referenced:
            print(f"spotlight_cleanup: {job['target']} is referenced again, leaving the object alone")
            if record_result(tx, job['id'], confirmed=True, error=None,
                             evidence=dict(referenced=True)) == 'done':
                done += 1
            else:
                missing += 1
    if not jobs:
        return dict(empty, halted=False)

    keys = [j['target'] for j in deletable]
    # Outside any transaction, and the only storage call in this module.
    # `delete_images` never raises and never blocks unboundedly, but a
    # caller-supplied `delete` might, so anything it does wrong costs a batch,
    # not a held lock.
    confirmed = set(delete(keys) or []) if keys else set()

    retried = abandoned = 0
    if deletable:
        with tx_factory() as tx:
            for job in deletable:
                key = job['target']
                ok = key in confirmed
                outcome = record_result(tx, job['id'], confirmed=ok,
                                        error=None if ok else 'deletion not confirmed by storage')
                if ok and outcome == 'done':
                    tx.execute(_Q_CLEAR_KEY, dict(k=key))
                    tx.execute(_Q_CLEAR_REVISION_KEY, dict(k=key))
                if outcome == 'done':
                    done += 1
                elif outcome == 'abandoned':
                    abandoned += 1
                elif outcome == 'missing':
                    missing += 1
                else:
                    retried += 1
    return dict(reserved=len(jobs), done=done, retried=retried, abandoned=abandoned,
                missing=missing, halted=False)
