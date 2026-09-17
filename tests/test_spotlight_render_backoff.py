"""Wave 3d Task 4 (acceptance 8c): old work is never starved.

The acceptance run found 1117 qualifying unrendered rows while the tick was
handed the newest 200, 8198 due removal tasks while the worker was handed the
newest 200, and 205 newer unrenderable rows hiding an older card for as long
as they kept failing. These tests reproduce each shape through the real Flask
client against the real test database.

The suite really commits and shares one database, so every test here places
its own rows ahead of whatever earlier tests left behind (backdated below the
current minimum) rather than assuming an empty table, and retires its rows at
the end so the next test's listings are not crowded by them. Helpers are
copied rather than imported: test files in this suite do not import from each
other.
"""
from __future__ import annotations

import base64
import hashlib
import io
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
from PIL import Image

from database import api_tx
from service.spotlight.queue import create_candidate

H = {'X-Growth-Cron': 'test-cron-secret'}
_SECOND = timedelta(seconds=1)

# The backoff the brief specifies: 15 minutes doubled per attempt already
# recorded, capped at 24 hours. Minutes, indexed by the attempt being recorded.
EXPECTED_BACKOFF_MINUTES = [15, 30, 60, 120, 240, 480, 960, 1440, 1440]


def _png_bytes(w=1080, h=1080, colour='white'):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), colour).save(buf, 'PNG')
    return buf.getvalue()


def _session_for(p) -> str:
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(i)s", dict(i=p['id'])).fetchone()['email']
        tx.execute(
            """INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
               VALUES (%(h)s, %(e)s, %(p)s, TRUE, '123456')""",
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=p['id']))
    return tok


def _earliest_created_at(tx):
    """Strictly earlier than every queue row already in the shared table."""
    return tx.execute(
        "SELECT COALESCE(MIN(created_at), NOW()) - interval '1 day' AS t FROM publishing_queue").fetchone()['t']


def _roundups(tx, n):
    return [create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
            for _ in range(n)]


def _date(tx, rk, at):
    tx.execute("UPDATE publishing_queue SET created_at = %(at)s WHERE request_key = %(rk)s", dict(at=at, rk=rk))


def _retire(keys):
    """Cancelled rows never qualify for `needs_render`, so they stop
    crowding the render listing for every later test."""
    with api_tx() as tx:
        tx.execute("UPDATE publishing_queue SET status = 'cancelled' WHERE request_key = ANY(%(k)s::text[])",
                   dict(k=list(keys)))


def _needs_render(client):
    r = client.get('/admin/growth/queue?needs_render=1', headers=H)
    assert r.status_code == 200
    return r.get_json()


def _fail(client, rk, reason='photo_missing', headers=H):
    return client.post(f'/admin/growth/queue/{rk}/render-failed', json={'reason': reason}, headers=headers)


# ---------------------------------------------------------------------------
# Step 2 (a): the 8c controlled demonstration
# ---------------------------------------------------------------------------

def test_an_older_card_is_served_first_once_newer_renders_back_off(client):
    """8c, reproduced: one older unrendered card, then 205 newer cards whose
    render keeps failing (410 rows, more than the listing's 200). Newest
    first, the older card was never handed to the tick at all. After each
    newer card has one render-failed call, the older card is first."""
    with api_tx() as tx:
        base = _earliest_created_at(tx)
        old_rk = _roundups(tx, 1)[0]
        newer = _roundups(tx, 205)
        _date(tx, old_rk, base)
        for i, rk in enumerate(newer, start=1):
            _date(tx, rk, base + i * _SECOND)
    try:
        # Oldest first on its own already reaches the older card, which the
        # newest-first listing never handed to the tick (410 newer rows).
        assert _needs_render(client)[0]['request_key'] == old_rk
        for rk in newer:
            r = _fail(client, rk)
            assert r.status_code == 200, (rk, r.get_json())
            assert r.get_json()['render_attempts'] == 1
        rows = _needs_render(client)
        assert rows[0]['request_key'] == old_rk
        assert not {r['request_key'] for r in rows} & set(newer)
    finally:
        _retire([old_rk, *newer])


def test_older_cards_in_backoff_do_not_starve_a_newer_card(client):
    """The other half of oldest-first: 205 OLDER cards that keep failing
    (410 rows) must not fill the 200-row listing ahead of a newer card that
    would render. Backoff is what hides them."""
    with api_tx() as tx:
        base = _earliest_created_at(tx)
        failing = _roundups(tx, 205)
        good_rk = _roundups(tx, 1)[0]
        for i, rk in enumerate(failing):
            _date(tx, rk, base + i * _SECOND)
        _date(tx, good_rk, base + 300 * _SECOND)
    try:
        for rk in failing:
            assert _fail(client, rk).status_code == 200
        rows = _needs_render(client)
        assert rows[0]['request_key'] == good_rk
        assert not {r['request_key'] for r in rows} & set(failing)
    finally:
        _retire([good_rk, *failing])


# ---------------------------------------------------------------------------
# Step 2 (b): backoff hides a row until its time passes
# ---------------------------------------------------------------------------

def test_a_card_in_backoff_is_not_listed_until_its_time_passes(client):
    with api_tx() as tx:
        base = _earliest_created_at(tx)
        rk = _roundups(tx, 1)[0]
        _date(tx, rk, base)
    keys = [rk]
    try:
        assert rk in {r['request_key'] for r in _needs_render(client)}
        body = _fail(client, rk).get_json()
        assert body['request_key'] == rk and body['render_attempts'] == 1
        assert body['render_next_attempt_at'] is not None
        assert rk not in {r['request_key'] for r in _needs_render(client)}
        # The Growth tab (no filter, newest first) still shows a card in
        # backoff, with its backoff state. `rk` is backdated out of the tab's
        # newest 200, so this looks at a fresh card instead.
        with api_tx() as tx:
            fresh = _roundups(tx, 1)[0]
        keys.append(fresh)
        assert _fail(client, fresh).status_code == 200
        tab = [r for r in client.get('/admin/growth/queue', headers=H).get_json() if r['request_key'] == fresh]
        assert len(tab) == 2
        assert all(r['render_attempts'] == 1 and r['render_next_attempt_at'] for r in tab)
        assert all(r['render_error'] == 'photo_missing' for r in tab)
        assert all(r['error'] is None for r in tab)
        with api_tx() as tx:
            tx.execute("""UPDATE publishing_queue SET render_next_attempt_at = NOW() - interval '1 second'
                           WHERE request_key = %(rk)s""", dict(rk=rk))
        assert rk in {r['request_key'] for r in _needs_render(client)}
    finally:
        _retire(keys)


def test_the_backoff_doubles_from_fifteen_minutes_and_caps_at_a_day(client):
    with api_tx() as tx:
        rk = _roundups(tx, 1)[0]
    try:
        observed = []
        for attempt, minutes in enumerate(EXPECTED_BACKOFF_MINUTES, start=1):
            body = _fail(client, rk).get_json()
            assert body['render_attempts'] == attempt
            with api_tx('read committed') as tx:
                rows = tx.execute(
                    """SELECT render_attempts,
                              extract(epoch FROM render_next_attempt_at - NOW()) / 60 AS minutes
                         FROM publishing_queue WHERE request_key = %(rk)s""", dict(rk=rk)).fetchall()
            assert {r['render_attempts'] for r in rows} == {attempt}
            for r in rows:
                assert abs(float(r['minutes']) - minutes) < 1, (attempt, float(r['minutes']))
            observed.append(round(float(rows[0]['minutes'])))
        assert observed == EXPECTED_BACKOFF_MINUTES
        # A card failing for months must never overflow the interval.
        with api_tx() as tx:
            tx.execute("UPDATE publishing_queue SET render_attempts = 100000 WHERE request_key = %(rk)s",
                       dict(rk=rk))
        r = _fail(client, rk)
        assert r.status_code == 200 and r.get_json()['render_attempts'] == 100001
        with api_tx('read committed') as tx:
            m = tx.execute("""SELECT extract(epoch FROM MAX(render_next_attempt_at) - NOW()) / 60 AS m
                                FROM publishing_queue WHERE request_key = %(rk)s""", dict(rk=rk)).fetchone()['m']
        assert abs(float(m) - 1440) < 1
    finally:
        _retire([rk])


# ---------------------------------------------------------------------------
# The render-failed route itself
# ---------------------------------------------------------------------------

def test_render_failed_reason_is_stripped_of_urls_and_truncated(client):
    with api_tx() as tx:
        rk = _roundups(tx, 1)[0]
    token = secrets.token_hex(16)
    reason = (f'photo_host_refused fetching https://cdn.example/p.png?X-Amz-Signature={token}&t=1 '
              f'then http://other.example/{token} ' + 'x' * 400)
    try:
        assert _fail(client, rk, reason).status_code == 200
        with api_tx('read committed') as tx:
            stored = {r['render_error'] for r in tx.execute(
                "SELECT render_error FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()}
        assert len(stored) == 1
        value = stored.pop()
        assert value.startswith('photo_host_refused fetching')
        assert token not in value and 'http' not in value
        assert len(value) <= 200
    finally:
        _retire([rk])


def test_render_failed_refuses_bad_bodies_unknown_keys_and_strangers(client, make_person):
    with api_tx() as tx:
        rk = _roundups(tx, 1)[0]
    try:
        assert client.post(f'/admin/growth/queue/{rk}/render-failed', json={}, headers=H).status_code == 400
        assert client.post(f'/admin/growth/queue/{rk}/render-failed', json={'reason': 7}, headers=H).status_code == 400
        assert _fail(client, 'no-such-request-key-' + secrets.token_hex(4)).status_code == 404
        assert _fail(client, rk, headers={'X-Growth-Cron': 'wrong'}).status_code == 403
        stranger = make_person(name='RenderStranger')
        assert _fail(client, rk, headers={'Authorization': f'Bearer {_session_for(stranger)}'}).status_code == 403
        admin = make_person(name='RenderAdmin')
        with api_tx() as tx:
            tx.execute("UPDATE person SET roles = ARRAY['admin']::TEXT[] WHERE id = %(i)s", dict(i=admin['id']))
        r = _fail(client, rk, headers={'Authorization': f'Bearer {_session_for(admin)}'})
        assert r.status_code == 200 and r.get_json()['render_attempts'] == 1
    finally:
        _retire([rk])


def test_a_successful_image_attach_resets_that_rows_backoff(client, monkeypatch):
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'put_card_image', lambda key, data, content_type, public=False: None)
    with api_tx() as tx:
        rk = _roundups(tx, 1)[0]
    try:
        assert _fail(client, rk).status_code == 200
        assert _fail(client, rk).status_code == 200
        png = base64.b64encode(_png_bytes()).decode()
        r = client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'facebook', 'png_base64': png}, headers=H)
        assert r.status_code == 200
        with api_tx('read committed') as tx:
            rows = {r['platform']: r for r in tx.execute(
                """SELECT platform, render_attempts, render_next_attempt_at, render_error
                     FROM publishing_queue WHERE request_key = %(rk)s""", dict(rk=rk)).fetchall()}
        assert (rows['facebook']['render_attempts'], rows['facebook']['render_next_attempt_at'],
                rows['facebook']['render_error']) == (0, None, None)
        assert rows['instagram']['render_attempts'] == 2
        assert rows['instagram']['render_error'] == 'photo_missing'
    finally:
        _retire([rk])


# A duplicate cron invocation can report the same card's failure twice at
# once. Both reports must count; neither may 500 on a serialization failure.

def _one_connection_per_tx(m):
    import database

    def _enter(self):
        self._own_conn = psycopg.Connection.connect(
            conninfo=database._api_conninfo, row_factory=psycopg.rows.dict_row)
        self.cur = self._own_conn.cursor()
        if self.isolation_level != database._default_transaction_isolation:
            self.cur.execute(f'SET TRANSACTION ISOLATION LEVEL {self.isolation_level}')
        return self.cur

    def _exit(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self._own_conn.commit()
            else:
                self._own_conn.rollback()
        finally:
            self._own_conn.close()

    m.setattr(database.api_tx, '__enter__', _enter)
    m.setattr(database.api_tx, '__exit__', _exit)


def _wait_for_lock_waiter(pids, timeout: float = 10.0) -> bool:
    import database
    deadline = time.monotonic() + timeout
    with psycopg.connect(database._api_conninfo, autocommit=True) as conn:
        while time.monotonic() < deadline:
            n = conn.execute(
                """SELECT count(*) FROM pg_stat_activity
                    WHERE pid = ANY(%(p)s) AND wait_event_type = 'Lock'""",
                dict(p=list(pids))).fetchone()[0]
            if n:
                return True
            time.sleep(0.02)
    return False


def test_two_racing_render_failed_reports_both_count(app, monkeypatch):
    import service.api.admin.spotlight_routes as sr
    with api_tx() as tx:
        rk = _roundups(tx, 1)[0]
    barrier = threading.Barrier(2)
    pids = set()
    first_done = threading.Event()
    real_lock = sr._lock_render_rows

    def racing_lock(tx, request_key):
        me = tx.connection.info.backend_pid
        pids.add(me)
        # Both transactions have already read before either locks: under a
        # snapshot isolation level the second would now fail to update.
        tx.execute("SELECT count(*) FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=request_key))
        barrier.wait(timeout=30)
        rows = real_lock(tx, request_key)
        if not first_done.is_set():
            first_done.set()
            # Hold the row locks until the other report is provably queued.
            assert _wait_for_lock_waiter(pids - {me})
        return rows

    try:
        with monkeypatch.context() as m:
            _one_connection_per_tx(m)
            m.setattr(sr, '_lock_render_rows', racing_lock)

            def worker(_i):
                with app.test_client() as c:
                    try:
                        r = _fail(c, rk)
                        return (r.status_code, r.get_json(silent=True))
                    except Exception as e:      # noqa: BLE001 -- recorded as the evidence
                        return type(e).__name__

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker, range(2)))
        assert [r[0] if isinstance(r, tuple) else r for r in results] == [200, 200], results
        assert sorted(r[1]['render_attempts'] for r in results) == [1, 2]
        with api_tx('read committed') as tx:
            attempts = {r['render_attempts'] for r in tx.execute(
                "SELECT render_attempts FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()}
        assert attempts == {2}
    finally:
        _retire([rk])


# ---------------------------------------------------------------------------
# Step 2 (c) and (d): removals are served by deadline; the tab is unchanged
# ---------------------------------------------------------------------------

def test_pending_removals_are_served_earliest_deadline_first(client):
    with api_tx() as tx:
        base = tx.execute(
            """SELECT COALESCE(MIN(deadline_at), NOW()) - interval '1 day' AS t
                 FROM spotlight_removal_task WHERE done_at IS NULL""").fetchone()['t']
        ids = [tx.execute(
            """INSERT INTO spotlight_removal_task
                      (platform, external_post_id, reason, next_attempt_at, deadline_at, created_at)
               VALUES ('facebook', %(ext)s, 'delete_via_api', NOW() - interval '1 minute',
                       %(deadline)s, NOW() - %(age)s * interval '1 millisecond')
               RETURNING id""",
            dict(ext=f'render-order-{i}', deadline=base + i * _SECOND, age=250 - i)).fetchone()['id']
            for i in range(250)]
    try:
        pending = client.get('/admin/growth/removals?pending=1', headers=H).get_json()['tasks']
        assert len(pending) == 200
        assert [t['id'] for t in pending] == ids[:200]
        # (d) The unfiltered list is unchanged: newest first. (The Growth
        # tab's removals panel reads `pending=1`, so it now sees deadline order.)
        listed = [t['id'] for t in client.get('/admin/growth/removals', headers=H).get_json()['tasks']
                  if t['id'] in set(ids)]
        assert listed == sorted(listed, key=ids.index, reverse=True)
        assert listed[0] == ids[-1]
    finally:
        with api_tx() as tx:
            tx.execute("UPDATE spotlight_removal_task SET done_at = NOW() WHERE id = ANY(%(i)s)", dict(i=ids))


def test_the_unfiltered_growth_queue_is_still_newest_first(client):
    with api_tx() as tx:
        keys = _roundups(tx, 3)
        for i, rk in enumerate(keys):
            tx.execute("""UPDATE publishing_queue SET created_at = NOW() - (3 - %(i)s) * interval '1 millisecond'
                           WHERE request_key = %(rk)s""", dict(i=i, rk=rk))
    try:
        seen = []
        for r in client.get('/admin/growth/queue', headers=H).get_json():
            if r['request_key'] in keys and r['request_key'] not in seen:
                seen.append(r['request_key'])
        assert seen == list(reversed(keys))
    finally:
        _retire(keys)


# ---------------------------------------------------------------------------
# Fix round 1
# ---------------------------------------------------------------------------

def test_a_good_card_is_reached_while_old_cards_fail_every_daily_tick(client, monkeypatch):
    """C1. The tick runs once a day and is handed 200 rows (100 cards). With
    150 older cards that fail every time, oldest first handed the tick the
    same 100 failing cards every day, because a backoff step under 24 hours
    has always passed by the next tick, and a newer card that would render
    was never reached. Ordered by when each card became eligible, a card
    that just failed goes behind the cards still waiting.

    Each round lists `needs_render` like the tick, reports every listed
    failing card and attaches the good card if it is listed, then advances
    the clock a day: every timestamp of these cards moves 24 hours back,
    which is what a day passing looks like from NOW()."""
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'put_card_image', lambda key, data, content_type, public=False: None)
    failing_count = 150
    bound = -(-failing_count // 100) + 1
    with api_tx() as tx:
        base = _earliest_created_at(tx)
        failing = _roundups(tx, failing_count)
        good_rk = _roundups(tx, 1)[0]
        for i, rk in enumerate(failing):
            _date(tx, rk, base + i * _SECOND)
        _date(tx, good_rk, base + (failing_count + 50) * _SECOND)
    mine = [*failing, good_rk]
    png = base64.b64encode(_png_bytes()).decode()
    reached = None
    try:
        for day in range(1, bound + 1):
            listed = []
            for r in _needs_render(client):
                if r['request_key'] not in listed:
                    listed.append(r['request_key'])
            for rk in listed:
                if rk == good_rk:
                    for platform in ('facebook', 'instagram'):
                        r = client.post(f'/admin/growth/queue/{rk}/image',
                                        json={'platform': platform, 'png_base64': png}, headers=H)
                        assert r.status_code == 200, r.get_json()
                    reached = day
                elif rk in failing:
                    assert _fail(client, rk).status_code == 200
            if reached:
                break
            with api_tx() as tx:
                tx.execute("""UPDATE publishing_queue
                                 SET created_at = created_at - interval '24 hours',
                                     render_next_attempt_at = render_next_attempt_at - interval '24 hours'
                               WHERE request_key = ANY(%(k)s::text[])""", dict(k=mine))
        assert reached is not None and reached <= bound, (reached, bound)
    finally:
        _retire(mine)


def test_the_worker_listing_reaches_a_delete_task_behind_overdue_manual_tasks(client):
    """I1. `pending=1` also carries `manual_instagram` and `investigate`
    tasks, which the worker skips without moving `next_attempt_at`. Once more
    than 200 of those are overdue and sorted first by deadline, the worker
    never received a `delete_via_api` task again. `worker=1` hands it only
    the tasks it can action."""
    with api_tx() as tx:
        base = tx.execute(
            """SELECT COALESCE(MIN(deadline_at), NOW()) - interval '1 day' AS t
                 FROM spotlight_removal_task WHERE done_at IS NULL""").fetchone()['t']
        manual = [tx.execute(
            """INSERT INTO spotlight_removal_task (platform, external_post_id, reason, next_attempt_at, deadline_at)
               VALUES ('instagram', %(ext)s, 'manual_instagram', NOW() - interval '1 minute', %(d)s)
               RETURNING id""", dict(ext=f'manual-{i}', d=base + i * _SECOND)).fetchone()['id']
            for i in range(250)]
        delete_id = tx.execute(
            """INSERT INTO spotlight_removal_task (platform, external_post_id, reason, next_attempt_at, deadline_at)
               VALUES ('facebook', 'delete-me', 'delete_via_api', NOW() - interval '1 minute', %(d)s)
               RETURNING id""", dict(d=base + 300 * _SECOND)).fetchone()['id']
    ids = [*manual, delete_id]
    try:
        worker = client.get('/admin/growth/removals?pending=1&worker=1', headers=H).get_json()['tasks']
        assert delete_id in [t['id'] for t in worker]
        assert {t['reason'] for t in worker} == {'delete_via_api'}
        # The operator panel keeps every actionable reason.
        panel = client.get('/admin/growth/removals?pending=1', headers=H).get_json()['tasks']
        assert [t['id'] for t in panel] == manual[:200]
    finally:
        with api_tx() as tx:
            tx.execute("UPDATE spotlight_removal_task SET done_at = NOW() WHERE id = ANY(%(i)s)", dict(i=ids))


def test_removals_with_the_same_deadline_are_served_in_id_order(client):
    """M5: a tie on deadline breaks by id, oldest task first."""
    with api_tx() as tx:
        deadline = tx.execute(
            """SELECT COALESCE(MIN(deadline_at), NOW()) - interval '1 day' AS t
                 FROM spotlight_removal_task WHERE done_at IS NULL""").fetchone()['t']
        ids = [tx.execute(
            """INSERT INTO spotlight_removal_task
                      (platform, external_post_id, reason, next_attempt_at, deadline_at, created_at)
               VALUES ('facebook', %(ext)s, 'delete_via_api', NOW() - interval '1 minute', %(d)s,
                       NOW() - %(age)s * interval '1 millisecond')
               RETURNING id""", dict(ext=f'tie-{i}', d=deadline, age=3 - i)).fetchone()['id']
            for i in range(3)]
    try:
        pending = client.get('/admin/growth/removals?pending=1', headers=H).get_json()['tasks']
        assert [t['id'] for t in pending[:3]] == ids
    finally:
        with api_tx() as tx:
            tx.execute("UPDATE spotlight_removal_task SET done_at = NOW() WHERE id = ANY(%(i)s)", dict(i=ids))


def test_a_new_revision_clears_the_render_backoff(client):
    """I2. A caption edit is new content: the card must be tried again at the
    next tick, not wait out the backoff its old artwork earned."""
    from service.spotlight.revisions import create_revision
    with api_tx() as tx:
        base = _earliest_created_at(tx)
        rk = _roundups(tx, 1)[0]
        _date(tx, rk, base)
    try:
        assert _fail(client, rk).status_code == 200
        assert _fail(client, rk).status_code == 200
        assert rk not in {r['request_key'] for r in _needs_render(client)}
        with api_tx() as tx:
            create_revision(tx, rk, caption='edited', photo_uuid=None, participants=[],
                            channels=['facebook', 'instagram'], created_by='t')
        listed = [r for r in _needs_render(client) if r['request_key'] == rk]
        assert len(listed) == 2
        assert all((r['render_attempts'], r['render_next_attempt_at'], r['render_error']) == (0, None, None)
                   for r in listed)
    finally:
        _retire([rk])


def test_render_failed_reason_drops_scheme_less_urls_and_signature_fragments(client):
    """M1: a signed URL is not always written with its scheme, and a
    signature can arrive on its own as a query fragment."""
    with api_tx() as tx:
        rk = _roundups(tx, 1)[0]
    token = secrets.token_hex(12)
    reason = (f'photo_host_refused //cdn.example/p.png?x={token} '
              f'X-Amz-Credential={token}/aws4_request Signature={token} '
              f'retry?token={token} &sig={token} done')
    try:
        assert _fail(client, rk, reason).status_code == 200
        with api_tx('read committed') as tx:
            value = tx.execute("SELECT render_error FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1",
                               dict(rk=rk)).fetchone()['render_error']
        assert value == 'photo_host_refused done'
    finally:
        _retire([rk])
