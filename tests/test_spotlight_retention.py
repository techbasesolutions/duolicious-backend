from database import api_tx
from service.cron.spotlightretention import retention_sweep, RETENTION_DAYS


OLD_KEY = 'spotlight/old1-facebook.png'
NEW_KEY = 'spotlight/new1-facebook.png'


def test_retention_queues_old_published_rows_and_clears_nothing():
    """Wave 2 Task 5 (F09): the sweep no longer deletes and no longer clears.
    It enqueues one asset_delete job per retained key and leaves the key
    exactly where it is -- only a confirmed deletion (the cleanup batch, in
    tests/test_spotlight_cleanup.py) may clear it. Nothing here touches
    storage, so nothing here needs monkeypatching."""
    with api_tx() as tx:
        # The test Postgres container persists data across separate `docker
        # compose run` invocations (there is no autouse rollback fixture in
        # this suite, unlike e.g. make_person's uuid4-per-row emails), so a
        # literal request_key must be cleaned up first to stay rerunnable.
        tx.execute("DELETE FROM publishing_queue WHERE request_key IN ('old1', 'new1')")
        tx.execute("DELETE FROM cleanup_job WHERE target = ANY(%(t)s::text[])",
                   dict(t=[OLD_KEY, NEW_KEY]))
        tx.execute("""INSERT INTO publishing_queue (request_key, kind, platform, status, image_key, image_url, image_sha256, updated_at)
                      VALUES ('old1', 'roundup', 'facebook', 'published', 'spotlight/old1-facebook.png', 'https://cdn/old1', 'oldsha', NOW() - interval '100 days'),
                             ('new1', 'roundup', 'facebook', 'published', 'spotlight/new1-facebook.png', 'https://cdn/new1', 'newsha', NOW() - interval '10 days')""")
        n = retention_sweep(tx)
        rows = {r['request_key']: r for r in tx.execute("SELECT request_key, image_key, image_sha256 FROM publishing_queue WHERE request_key IN ('old1','new1')").fetchall()}
        jobs = {r['target']: r['state'] for r in tx.execute(
            "SELECT target, state FROM cleanup_job WHERE target = ANY(%(t)s::text[])",
            dict(t=[OLD_KEY, NEW_KEY])).fetchall()}
    assert n >= 1
    # The old row is queued for cleanup; the row still inside the retention
    # window is not queued at all.
    assert jobs.get(OLD_KEY) == 'pending' and NEW_KEY not in jobs
    # Neither row's key is touched by the sweep: the object is still there
    # until a job confirms otherwise.
    assert rows['old1']['image_key'] == OLD_KEY and rows['new1']['image_key'] == NEW_KEY
    assert rows['old1']['image_sha256'] == 'oldsha' and rows['new1']['image_sha256'] == 'newsha'
    assert RETENTION_DAYS == 90


ABANDONED_KEY = 'spotlight/abandoned1-facebook.png'


def test_retention_stops_re_queueing_a_key_whose_job_was_abandoned():
    """Fix wave item 3. An abandoned job is left in the table on purpose: it
    is the only record that an object may still be sitting in the bucket, and
    a human has to look. But the queue row keeps its key (nothing confirmed
    the deletion), so without this guard the next sweep would see the row
    again, find no PENDING job for the key, and file a fresh one -- which
    would fail its way to abandoned all over again, every day, burying the
    original alert under duplicates. The row drops out of the sweep instead
    and stays visible through `abandoned_cleanup` on the removals surface."""
    with api_tx() as tx:
        tx.execute("DELETE FROM publishing_queue WHERE request_key = 'abandoned1'")
        tx.execute("DELETE FROM cleanup_job WHERE target = %(t)s", dict(t=ABANDONED_KEY))
        tx.execute("""INSERT INTO publishing_queue (request_key, kind, platform, status, image_key, image_url, updated_at)
                      VALUES ('abandoned1', 'roundup', 'facebook', 'published', %(k)s, 'https://cdn/abandoned1',
                              NOW() - interval '100 days')""", dict(k=ABANDONED_KEY))
        tx.execute("""INSERT INTO cleanup_job (kind, target, state, last_error)
                      VALUES ('asset_delete', %(k)s, 'abandoned', 'deletion not confirmed by storage')""",
                   dict(k=ABANDONED_KEY))
        retention_sweep(tx)
        jobs = tx.execute(
            "SELECT state FROM cleanup_job WHERE target = %(t)s ORDER BY id", dict(t=ABANDONED_KEY)).fetchall()
    assert [r['state'] for r in jobs] == ['abandoned']


# Storage-level delete_images tests (validate_card_image, put_card_image, and the
# confirmed-deletion / batching / unconfigured-noop behaviour of
# delete_images itself) moved to tests/test_spotlight_storage.py in
# Wave 2 Task 2, updated there for the new list-of-confirmed-keys return
# type.
