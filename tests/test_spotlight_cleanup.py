"""Durable cleanup jobs for spotlight artwork (Wave 2 Task 5, F09 part 2).

The rule these tests exist to hold: a key is never cleared from
`publishing_queue` until a cleanup job has CONFIRMED the object is gone from
storage. Every producer (the retention sweep, the removal-done route, a
withdrawal's cancelled rows, a superseded upload, a caption edit that
re-points a rendered request) enqueues a job inside its own transaction and
clears nothing; `run_cleanup_batch` is the only place storage deletion
happens, and it runs OUTSIDE its own transactions.

The test environment has non-blank R2 credentials and no reachable object
store, so every test here either passes an explicit `delete=` callable to
`run_cleanup_batch` or never lets a batch run at all. Nothing in this file
may reach the network.

The test database persists across `docker compose run` invocations (there is
no autouse rollback fixture in this suite), so every test that uses a literal
cleanup target deletes its own rows first to stay rerunnable.

Helpers `_make_eligible`, `H` and the render stand-in are copied from
tests/test_spotlight_routes.py on purpose -- test files in this suite do not
import from each other.
"""
from __future__ import annotations

import hashlib

from database import api_tx
from service.cron.spotlightretention import retention_sweep
from service.spotlight import cleanup
from service.spotlight.assets import asset_key, attach_platform_image, complete_render_if_ready
from service.spotlight.queue import create_candidate, set_setting
from service.spotlight.revisions import attach_render, current_revision, edit_caption

H = {'X-Growth-Cron': 'test-cron-secret'}


def _make_eligible(make_person, name='Elig', gender='Woman'):
    p = make_person(name=name, gender=gender)
    with api_tx() as tx:
        tx.execute("""
            UPDATE person SET spotlight_opt_in = TRUE, spotlight_opt_in_at = NOW(),
                   ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01',
                   deletion_requested_at = NULL, spotlight_last_featured_at = NULL
             WHERE id = %(id)s""", dict(id=p['id']))
        tx.execute("""
            INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
            VALUES (gen_random_uuid(), %(id)s, 1, 'approved', 'testblurhash', gen_random_uuid()::text)""",
                   dict(id=p['id']))
    return p


def _forget(*targets):
    """Drop any cleanup_job row for these targets so a rerun starts clean."""
    with api_tx() as tx:
        tx.execute("DELETE FROM cleanup_job WHERE target = ANY(%(t)s::text[])", dict(t=list(targets)))


def _jobs(*targets) -> dict:
    with api_tx('read committed') as tx:
        return {r['target']: r for r in tx.execute(
            """SELECT target, state, attempts, next_attempt_at > NOW() AS later
                 FROM cleanup_job WHERE target = ANY(%(t)s::text[])""",
            dict(t=list(targets))).fetchall()}


# ---------------------------------------------------------------------------
# The job table itself
# ---------------------------------------------------------------------------

def test_enqueue_is_idempotent():
    key = 'spotlight/x/1-abc-facebook.png'
    _forget(key)
    with api_tx() as tx:
        assert cleanup.enqueue_asset_delete(tx, key) is not None
        assert cleanup.enqueue_asset_delete(tx, key) is None


def test_partial_confirmation_retains_unconfirmed_keys():
    _forget('k-ok', 'k-bad')
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, 'k-ok')
        cleanup.enqueue_asset_delete(tx, 'k-bad')
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: [k for k in keys if k == 'k-ok'])
    assert out['halted'] is False and out['done'] >= 1 and out['retried'] >= 1
    rows = _jobs('k-ok', 'k-bad')
    assert rows['k-ok']['state'] == 'done'
    assert rows['k-bad']['state'] == 'pending' and rows['k-bad']['later'] is True


def test_abandon_after_max_attempts(capsys):
    _forget('k-never')
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, 'k-never')
        tx.execute("UPDATE cleanup_job SET attempts = %(a)s, next_attempt_at = NOW() WHERE target = 'k-never'",
                   dict(a=cleanup.MAX_ATTEMPTS - 1))
    cleanup.run_cleanup_batch(api_tx, delete=lambda keys: [])
    assert _jobs('k-never')['k-never']['state'] == 'abandoned'
    assert 'ABANDONED k-never' in capsys.readouterr().out


def test_emergency_stop_halts_cleanup_and_counts_outstanding():
    _forget('k-halt')
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, 'k-halt')
        set_setting(tx, 'external_access_enabled', 'false')
    try:
        calls = []
        out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: calls.append(keys) or [])
        assert out['halted'] is True and out['outstanding'] >= 1 and calls == []
        assert _jobs('k-halt')['k-halt']['state'] == 'pending'
    finally:
        with api_tx() as tx:
            set_setting(tx, 'external_access_enabled', 'true')


# ---------------------------------------------------------------------------
# The producers: keys survive until a job confirms
# ---------------------------------------------------------------------------

def test_retention_keeps_keys_until_the_job_confirms(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h',
                      f'spotlight/{rk}/1-abc-facebook.png', 'https://cdn/x.png')
        tx.execute(
            """UPDATE publishing_queue
                  SET status = 'published', external_post_id = '1',
                      image_key = 'spotlight/' || request_key || '/1-abc-' || platform || '.png',
                      image_url = 'https://cdn/x.png', updated_at = NOW() - interval '91 days'
                WHERE request_key = %(rk)s""", dict(rk=rk))
        # The sweep is global (it has no request filter), and it no longer
        # clears keys, so rows left behind by an earlier test or an earlier
        # run of this suite are swept again on every call. Both platform rows
        # of THIS request are what the assertions below pin down.
        assert retention_sweep(tx) >= 2
        rows = tx.execute("SELECT image_key FROM publishing_queue WHERE request_key = %(rk)s",
                          dict(rk=rk)).fetchall()
        assert all(r['image_key'] for r in rows)            # nothing cleared yet
        jobs = tx.execute("SELECT target, state FROM cleanup_job WHERE target LIKE %(pfx)s",
                          dict(pfx=f'spotlight/{rk}/' + '%')).fetchall()
        assert len(jobs) == 2 and {j['state'] for j in jobs} == {'pending'}
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: list(keys))    # storage confirms everything
    assert out['done'] >= 2
    with api_tx('read committed') as tx:
        rows = tx.execute("SELECT image_key, image_url FROM publishing_queue WHERE request_key = %(rk)s",
                          dict(rk=rk)).fetchall()
    assert all(r['image_key'] is None and r['image_url'] is None for r in rows)


def test_removal_done_enqueues_and_keeps_key_until_confirmed(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            """UPDATE publishing_queue
                  SET status = 'published', external_post_id = '7',
                      image_key = 'spotlight/' || request_key || '/1-abc-' || platform || '.png'
                WHERE request_key = %(rk)s""", dict(rk=rk))
        from service.spotlight.withdrawal import withdraw_member
        withdraw_member(tx, p['id'], 'opt_out')
        task_id = tx.execute(
            "SELECT id FROM spotlight_removal_task WHERE request_key = %(rk)s AND platform = 'facebook'",
            dict(rk=rk)).fetchone()['id']
    assert client.post(f'/admin/growth/removals/{task_id}/done', json={}, headers=H).status_code == 200
    with api_tx('read committed') as tx:
        assert tx.execute(
            "SELECT image_key FROM publishing_queue WHERE request_key = %(rk)s AND platform = 'facebook'",
            dict(rk=rk)).fetchone()['image_key'] is not None
        assert tx.execute(
            "SELECT count(*) AS n FROM cleanup_job WHERE target = %(t)s AND state = 'pending'",
            dict(t=f'spotlight/{rk}/1-abc-facebook.png')).fetchone()['n'] == 1


def test_overdue_removals_counted(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'published', external_post_id = '8' WHERE request_key = %(rk)s",
                   dict(rk=rk))
        from service.spotlight.withdrawal import withdraw_member
        withdraw_member(tx, p['id'], 'opt_out')
        tx.execute("UPDATE spotlight_removal_task SET deadline_at = NOW() - interval '1 hour' WHERE request_key = %(rk)s",
                   dict(rk=rk))
        assert cleanup.overdue_removals(tx) >= 2
    body = client.get('/admin/growth/removals?pending=1', headers=H).get_json()
    assert body['overdue'] >= 2 and 'outstanding_cleanup' in body


def test_withdrawal_enqueues_cancelled_keys_instead_of_deleting(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            """UPDATE publishing_queue
                  SET image_key = 'spotlight/' || request_key || '/1-cancel-' || platform || '.png',
                      image_url = 'https://cdn/c.png'
                WHERE request_key = %(rk)s""", dict(rk=rk))
        from service.spotlight.withdrawal import withdraw_member
        out = withdraw_member(tx, p['id'], 'opt_out')
        assert out['cancelled'] == 2
        jobs = tx.execute(
            "SELECT target, state FROM cleanup_job WHERE target LIKE %(pfx)s",
            dict(pfx=f'spotlight/{rk}/1-cancel-' + '%')).fetchall()
        assert len(jobs) == 2 and {j['state'] for j in jobs} == {'pending'}
        # The key itself is still on the row: only a confirmed deletion clears it.
        rows = tx.execute("SELECT image_key FROM publishing_queue WHERE request_key = %(rk)s",
                          dict(rk=rk)).fetchall()
        assert all(r['image_key'] for r in rows)


# ---------------------------------------------------------------------------
# Task 3 re-review: a caption edit un-renders the request it re-points
# ---------------------------------------------------------------------------

def test_caption_edit_unrenders_every_repointed_row_and_enqueues_the_old_keys(make_person):
    """A rendered request whose caption is edited moves to a new revision, so
    the artwork on every row it re-points no longer matches what the row says
    it shows. Both rows go back to un-rendered, both old keys get a cleanup
    job, and one fresh platform upload can no longer complete the render."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        rev1 = current_revision(tx, rk)['id']
        old = {}
        for platform in ('facebook', 'instagram'):
            sha = hashlib.sha256(f'{rk}-{platform}'.encode()).hexdigest()
            key = asset_key(rk, rev1, sha, platform)
            old[platform] = key
            tx.execute(
                """UPDATE publishing_queue SET image_key = %(k)s, image_url = %(u)s, image_sha256 = %(h)s,
                          status = 'review', updated_at = NOW()
                    WHERE request_key = %(rk)s AND platform = %(pl)s""",
                dict(k=key, u=f'https://cdn/{platform}.png', h=sha, rk=rk, pl=platform))
        assert complete_render_if_ready(tx, rk, rev1) is True

        rev2 = edit_caption(tx, rk, 'a different caption', 't')
        rows = tx.execute(
            "SELECT image_key, image_url, image_sha256 FROM publishing_queue WHERE request_key = %(rk)s",
            dict(rk=rk)).fetchall()
        assert all(r['image_key'] is None and r['image_url'] is None and r['image_sha256'] is None for r in rows)

        # Only facebook is re-uploaded against the new revision.
        sha2 = hashlib.sha256(f'{rk}-facebook-2'.encode()).hexdigest()
        key2 = asset_key(rk, rev2, sha2, 'facebook')
        assert attach_platform_image(tx, rk, 'facebook', rev2, key2, 'https://cdn/fb2.png', sha2) == 'attached'
        assert complete_render_if_ready(tx, rk, rev2) is False

    rows = _jobs(old['facebook'], old['instagram'])
    assert set(rows) == {old['facebook'], old['instagram']}
    assert {r['state'] for r in rows.values()} == {'pending'}
