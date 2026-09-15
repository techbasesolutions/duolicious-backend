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
    open, and a second transaction records each result -- and clears the queue
    row's image columns only for the keys storage actually CONFIRMED.

So a key is never cleared until its deletion is confirmed, and a failure is a
retry with backoff rather than a silent orphan. A job that exhausts
`MAX_ATTEMPTS` is abandoned with an alert line and LEFT in the table: nothing
sweeps it up, because at that point a human needs to look at the bucket.

`settings` is imported lazily inside `run_cleanup_batch`: `service.spotlight.
queue` imports `service.spotlight.revisions` at load time and `revisions`
imports `enqueue_asset_delete` from here, so a top-level import back to
`queue` would be a cycle. Same reasoning (and same shape) as `approve_card`'s
lazy import in `revisions.py`.
"""
from __future__ import annotations

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

_Q_ENQUEUE = """
    INSERT INTO cleanup_job (kind, target)
    VALUES ('asset_delete', %(t)s)
    ON CONFLICT (kind, target) DO NOTHING
    RETURNING id
"""

# The inner SELECT takes the row locks with SKIP LOCKED, so a second batch
# running at the same moment never blocks on -- or double-reserves -- the rows
# this one is taking. The attempts bump is the reservation: a batch that dies
# after the storage call but before recording results still leaves the attempt
# counted, which is the honest reading (the delete may well have happened).
_Q_DUE = """
    UPDATE cleanup_job
       SET attempts = attempts + 1
     WHERE id IN (
        SELECT id FROM cleanup_job
         WHERE state = 'pending' AND next_attempt_at <= NOW()
         ORDER BY id
         LIMIT %(n)s
         FOR UPDATE SKIP LOCKED
     )
    RETURNING id, kind, target, attempts
"""

_Q_DONE = """
    UPDATE cleanup_job SET state = 'done', done_at = NOW(), last_error = NULL
     WHERE id = %(id)s
"""

_Q_ABANDON = """
    UPDATE cleanup_job SET state = 'abandoned', last_error = %(e)s WHERE id = %(id)s
"""

_Q_RETRY = """
    UPDATE cleanup_job
       SET next_attempt_at = NOW() + make_interval(secs => %(s)s), last_error = %(e)s
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

_Q_OUTSTANDING = "SELECT count(*) AS n FROM cleanup_job WHERE state = 'pending'"

_Q_OVERDUE_REMOVALS = """
    SELECT count(*) AS n FROM spotlight_removal_task
     WHERE done_at IS NULL AND deadline_at < NOW()
"""


def enqueue_asset_delete(tx, key: str) -> Optional[int]:
    """Record that one stored object is finished with. Runs inside the
    caller's transaction: this is a database write, not an outbound call, so
    it commits or rolls back with whatever decided the artwork is done.

    Returns the new job id, or None when there is already a job for this key
    (the unique `(kind, target)` index is the idempotency point, so a retried
    request, a re-run sweep or two producers naming the same key add nothing).
    A blank key is a no-op rather than a row: nothing to delete."""
    if not key:
        return None
    row = tx.execute(_Q_ENQUEUE, dict(t=key)).fetchone()
    return row['id'] if row else None


def due_jobs(tx, limit: int = BATCH) -> list[dict]:
    """Reserve up to `limit` pending jobs whose next attempt has come round,
    oldest first, bumping each one's attempt count. Returns the reserved rows
    (id, kind, target, attempts)."""
    return tx.execute(_Q_DUE, dict(n=limit)).fetchall()


def record_result(tx, job_id, *, confirmed: bool, error: Optional[str]) -> str:
    """File one job's outcome. Returns 'done', 'pending' (requeued with
    backoff) or 'abandoned'.

    `attempts` is read back rather than passed in because `due_jobs` already
    bumped it: the number this reads is the attempt that just happened, so
    reaching MAX_ATTEMPTS here means the job has genuinely had that many
    goes."""
    row = tx.execute("SELECT target, attempts FROM cleanup_job WHERE id = %(id)s",
                     dict(id=job_id)).fetchone()
    if not row:
        return 'done'
    if confirmed:
        tx.execute(_Q_DONE, dict(id=job_id))
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


def overdue_removals(tx) -> int:
    """Open removal tasks past their deadline. A removal order that nobody
    actions is the failure mode that matters most here: the member asked to be
    taken down and the post is still up."""
    return int(tx.execute(_Q_OVERDUE_REMOVALS).fetchone()['n'])


def run_cleanup_batch(tx_factory, delete: Callable[[list], list] = delete_images) -> dict:
    """Delete one batch of due objects. Returns
    dict(reserved, done, retried, abandoned, halted=False), or
    dict(halted=True, outstanding=<pending jobs>) when the emergency stop is
    engaged.

    Transaction shape, which is the whole point of this function: ONE
    transaction reads the stop and reserves the due jobs; then `delete` runs
    with NO transaction open; then ONE transaction records every result and
    clears the queue rows for the confirmed keys. The delete call is a network
    round trip to a third party, and holding row locks across it is how a
    storage outage becomes a database outage.

    `external_access_enabled` is the same emergency stop every outbound call
    obeys (Task 9, F12). Deleting an object is outbound, so a stop means no
    delete call is made at all and every job stays exactly as it is -- not
    reserved, not attempted, not backed off."""
    from service.spotlight.queue import settings  # lazy: see module docstring
    with tx_factory() as tx:
        if settings(tx).get('external_access_enabled') != 'true':
            return dict(halted=True, outstanding=outstanding_jobs(tx))
        jobs = due_jobs(tx)
    if not jobs:
        return dict(reserved=0, done=0, retried=0, abandoned=0, halted=False)

    keys = [j['target'] for j in jobs]
    # Outside any transaction, and the only storage call in this module.
    # `delete_images` never raises and never blocks unboundedly, but a
    # caller-supplied `delete` might, so anything it does wrong costs a batch,
    # not a held lock.
    confirmed = set(delete(keys) or [])

    done = retried = abandoned = 0
    with tx_factory() as tx:
        for job in jobs:
            key = job['target']
            ok = key in confirmed
            outcome = record_result(tx, job['id'], confirmed=ok,
                                    error=None if ok else 'deletion not confirmed by storage')
            if ok:
                tx.execute(_Q_CLEAR_KEY, dict(k=key))
            if outcome == 'done':
                done += 1
            elif outcome == 'abandoned':
                abandoned += 1
            else:
                retried += 1
    return dict(reserved=len(jobs), done=done, retried=retried, abandoned=abandoned, halted=False)
