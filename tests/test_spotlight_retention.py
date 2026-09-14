from database import api_tx
from service.cron.spotlightretention import retention_sweep, RETENTION_DAYS


def test_retention_sweeps_old_published_rows(monkeypatch):
    import service.cron.spotlightretention as m
    deleted = []
    monkeypatch.setattr(m, 'delete_images', lambda keys: deleted.extend(keys) or len(keys))
    with api_tx() as tx:
        # The test Postgres container persists data across separate `docker
        # compose run` invocations (there is no autouse rollback fixture in
        # this suite, unlike e.g. make_person's uuid4-per-row emails), so a
        # literal request_key must be cleaned up first to stay rerunnable.
        tx.execute("DELETE FROM publishing_queue WHERE request_key IN ('old1', 'new1')")
        tx.execute("""INSERT INTO publishing_queue (request_key, kind, platform, status, image_key, image_url, updated_at)
                      VALUES ('old1', 'roundup', 'facebook', 'published', 'spotlight/old1-facebook.png', 'https://cdn/old1', NOW() - interval '100 days'),
                             ('new1', 'roundup', 'facebook', 'published', 'spotlight/new1-facebook.png', 'https://cdn/new1', NOW() - interval '10 days')""")
        n = retention_sweep(tx)
        rows = {r['request_key']: r for r in tx.execute("SELECT request_key, image_key FROM publishing_queue WHERE request_key IN ('old1','new1')").fetchall()}
    assert n >= 1 and 'spotlight/old1-facebook.png' in deleted and 'spotlight/new1-facebook.png' not in deleted
    assert rows['old1']['image_key'] is None and rows['new1']['image_key'] is not None
    assert RETENTION_DAYS == 90


def test_delete_images_swallows_errors(monkeypatch):
    import service.spotlight.storage as st
    class _B:
        def delete_objects(self, **kw): raise RuntimeError('boom')
    monkeypatch.setattr(st, '_bucket', lambda: _B())
    assert st.delete_images(['a', 'b']) == 2      # requested count, no raise
    assert st.delete_images([]) == 0
