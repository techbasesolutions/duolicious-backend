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


# Storage-level delete_images tests (validate_png, put_png, and the
# confirmed-deletion / batching / unconfigured-noop behaviour of
# delete_images itself) moved to tests/test_spotlight_storage.py in
# Wave 2 Task 2, updated there for the new list-of-confirmed-keys return
# type.
