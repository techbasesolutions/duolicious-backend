"""Durable cleanup jobs for spotlight artwork (Wave 2 Task 5, F09 part 2).

The rule these tests exist to hold: a key is never cleared from
`publishing_queue` until a cleanup job has CONFIRMED the object is gone from
storage. Every producer (the retention sweep, the removal-done route, a
withdrawal's cancelled rows, a superseded upload, a caption edit that
re-points a rendered request) enqueues a job inside its own transaction and
clears nothing; `run_cleanup_batch` is the only place storage deletion
happens, and it runs OUTSIDE its own transactions.

Fix round 1 adds two guards. Object keys are content-hashed, so the same key
can be created, retired and created again: uniqueness is therefore over
PENDING jobs only (migration 0047), and the batch re-checks every reserved
job against the rows that name its key before handing it to storage. A
reference that was NOT there when the job was queued means the key came back
to life, so the object is left alone and the job is filed done with
`evidence.referenced`.

The test environment has non-blank R2 credentials and no reachable object
store, so every test here either passes an explicit `delete=` callable to
`run_cleanup_batch` or never lets a batch run at all. Nothing in this file
may reach the network.

The test database persists across `docker compose run` invocations (there is
no autouse rollback fixture in this suite) AND the cleanup table is shared, so
every test mints a unique key prefix with `_pfx()` and passes it to
`run_cleanup_batch` as `target_prefix`. A batch is then blind to every other
test's jobs, whatever order the suite runs in.

Helpers `_make_eligible`, `H` and the render stand-in are copied from
tests/test_spotlight_routes.py on purpose -- test files in this suite do not
import from each other.
"""
from __future__ import annotations

import hashlib
from uuid import uuid4

from database import api_tx
from service.cron.spotlightretention import retention_sweep
from service.spotlight import cleanup
from service.spotlight.approval import card_state
from service.spotlight.assets import asset_key, attach_platform_image, complete_render_if_ready
from service.spotlight.queue import create_candidate, set_setting
from service.spotlight.revisions import attach_render, current_revision, edit_caption

H = {'X-Growth-Cron': 'test-cron-secret'}


def _pfx() -> str:
    """A key namespace no other test can collide with, in this run or any
    earlier one. Passed to `run_cleanup_batch` so the batch only ever sees
    this test's own jobs."""
    return f'test/{uuid4().hex}/'


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


def _jobs(*targets) -> dict:
    with api_tx('read committed') as tx:
        return {r['target']: r for r in tx.execute(
            """SELECT target, state, attempts, evidence, next_attempt_at > NOW() AS later
                 FROM cleanup_job WHERE target = ANY(%(t)s::text[])""",
            dict(t=list(targets))).fetchall()}


# ---------------------------------------------------------------------------
# The job table itself
# ---------------------------------------------------------------------------

def test_enqueue_is_idempotent():
    key = _pfx() + '1-abc-facebook.png'
    with api_tx() as tx:
        assert cleanup.enqueue_asset_delete(tx, key) is not None
        assert cleanup.enqueue_asset_delete(tx, key) is None


def test_a_done_job_does_not_block_a_new_one_for_the_same_key():
    """Fix round 1, ruling 1. Keys are content-hashed, so the same key can be
    created, retired, created again and retired again. Migration 0047 makes
    uniqueness partial over `state = 'pending'`: two open jobs for one key
    collapse to one, but a job that is already done blocks nothing."""
    pfx = _pfx()
    key = pfx + 'reusable.png'
    with api_tx() as tx:
        first = cleanup.enqueue_asset_delete(tx, key)
        assert first is not None
        assert cleanup.enqueue_asset_delete(tx, key) is None     # two pending collapse to one
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: list(keys), target_prefix=pfx)
    assert out['done'] == 1 and _jobs(key)[key]['state'] == 'done'
    with api_tx() as tx:
        second = cleanup.enqueue_asset_delete(tx, key)
    assert second is not None and second != first
    with api_tx('read committed') as tx:
        rows = tx.execute("SELECT state FROM cleanup_job WHERE target = %(t)s ORDER BY id",
                          dict(t=key)).fetchall()
    assert [r['state'] for r in rows] == ['done', 'pending']


def test_partial_confirmation_retains_unconfirmed_keys():
    pfx = _pfx()
    ok, bad = pfx + 'ok', pfx + 'bad'
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, ok)
        cleanup.enqueue_asset_delete(tx, bad)
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: [k for k in keys if k == ok],
                                    target_prefix=pfx)
    assert out['halted'] is False and out['reserved'] == 2
    assert out['done'] == 1 and out['retried'] == 1
    rows = _jobs(ok, bad)
    assert rows[ok]['state'] == 'done'
    assert rows[bad]['state'] == 'pending' and rows[bad]['later'] is True


def test_abandon_after_max_attempts(capsys):
    pfx = _pfx()
    key = pfx + 'never'
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, key)
        tx.execute("UPDATE cleanup_job SET attempts = %(a)s, next_attempt_at = NOW() WHERE target = %(t)s",
                   dict(a=cleanup.MAX_ATTEMPTS - 1, t=key))
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: [], target_prefix=pfx)
    assert out['abandoned'] == 1
    assert _jobs(key)[key]['state'] == 'abandoned'
    assert f'ABANDONED {key}' in capsys.readouterr().out


def test_a_job_that_vanished_mid_batch_is_counted_missing():
    """Fix round 1, ruling 4. A job deleted between the reservation and the
    result write is not a confirmed deletion and must not be counted as one."""
    pfx = _pfx()
    key = pfx + 'gone'
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, key)

    def _delete(keys):
        # Runs with NO transaction open (that is the batch's contract), so
        # opening one here is safe and also proves the contract.
        with api_tx() as tx:
            tx.execute("DELETE FROM cleanup_job WHERE target = ANY(%(t)s::text[])", dict(t=list(keys)))
        return list(keys)

    out = cleanup.run_cleanup_batch(api_tx, delete=_delete, target_prefix=pfx)
    assert out['reserved'] == 1 and out['missing'] == 1 and out['done'] == 0


def test_record_result_on_a_vanished_job_reports_missing():
    with api_tx() as tx:
        assert cleanup.record_result(tx, 10 ** 15, confirmed=True, error=None) == 'missing'


def test_emergency_stop_halts_cleanup_and_counts_outstanding():
    pfx = _pfx()
    key = pfx + 'halt'
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, key)
        set_setting(tx, 'external_access_enabled', 'false')
    try:
        calls = []
        out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: calls.append(keys) or [],
                                        target_prefix=pfx)
        assert out['halted'] is True and out['outstanding'] >= 1 and calls == []
        # Fix round 1, ruling 4: the halted answer carries the same counters
        # as a normal batch, all zero, so a caller never has to special-case
        # the shape.
        assert out['reserved'] == 0 and out['done'] == 0 and out['retried'] == 0
        assert out['abandoned'] == 0 and out['missing'] == 0
        # Nothing was reserved, so the job is untouched: same state, same
        # attempt count. A stop must not spend a job's retry budget.
        row = _jobs(key)[key]
        assert row['state'] == 'pending' and row['attempts'] == 0
    finally:
        with api_tx() as tx:
            set_setting(tx, 'external_access_enabled', 'true')


# ---------------------------------------------------------------------------
# Fix round 1, ruling 2: a key that came back to life is never deleted
# ---------------------------------------------------------------------------

def test_a_key_referenced_again_after_enqueue_is_not_deleted(make_person):
    """Object keys are content-hashed, so a re-upload of identical bytes
    re-creates the exact key a job is already queued for. The batch re-checks
    every reserved job against the rows naming its key and skips any reference
    that was not there when the job was queued: the object stays, the key
    stays, and the job is filed done with `evidence.referenced`."""
    pfx = _pfx()
    key = pfx + 'reborn.png'
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        # Queued while nothing references the key.
        assert cleanup.enqueue_asset_delete(tx, key) is not None
    with api_tx() as tx:
        # ... and then the key comes back, on a live row.
        tx.execute("""UPDATE publishing_queue SET image_key = %(k)s, image_url = 'https://cdn/reborn.png'
                       WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(k=key, rk=rk))
    calls = []
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: calls.append(list(keys)) or list(keys),
                                    target_prefix=pfx)
    assert calls == []                      # never handed to storage
    assert out['reserved'] == 1 and out['done'] == 1
    job = _jobs(key)[key]
    assert job['state'] == 'done' and job['evidence'].get('referenced') is True
    with api_tx('read committed') as tx:
        row = tx.execute(
            "SELECT image_key FROM publishing_queue WHERE request_key = %(rk)s AND platform = 'facebook'",
            dict(rk=rk)).fetchone()
    assert row['image_key'] == key          # neither deleted nor cleared


def test_the_row_that_owned_the_key_at_enqueue_does_not_block_its_own_cleanup(make_person):
    """The counterpart to the test above. Retention, a removal marked done and
    a withdrawal all queue a key that is still ON its row -- that is the whole
    point of the retained-key rule. The reference recorded at enqueue time is
    the job's own, so it never blocks; only a NEW one does."""
    pfx = _pfx()
    key = pfx + 'retired.png'
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue SET image_key = %(k)s, image_url = 'https://cdn/r.png'
                       WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(k=key, rk=rk))
        assert cleanup.enqueue_asset_delete(tx, key) is not None
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: list(keys), target_prefix=pfx)
    assert out['done'] == 1
    assert _jobs(key)[key]['evidence'].get('referenced') is None
    with api_tx('read committed') as tx:
        row = tx.execute(
            "SELECT image_key FROM publishing_queue WHERE request_key = %(rk)s AND platform = 'facebook'",
            dict(rk=rk)).fetchone()
    assert row['image_key'] is None


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
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: list(keys),   # storage confirms everything
                                    target_prefix=f'spotlight/{rk}/')
    assert out['done'] == 2
    with api_tx('read committed') as tx:
        rows = tx.execute("SELECT image_key, image_url FROM publishing_queue WHERE request_key = %(rk)s",
                          dict(rk=rk)).fetchall()
    assert all(r['image_key'] is None and r['image_url'] is None for r in rows)


def test_confirmed_deletion_clears_the_revision_columns_too(make_person):
    """Fix wave item 4. `spotlight_revision` names the object as well as the
    queue row does, and it is the revision the member's own card screen reads
    (`card_state.image_url` presigns `revision.image_key`). Clearing only the
    queue row left the revision pointing at an object that is provably gone,
    so a member opening their card after a retention sweep or a withdrawal
    got a signed URL for nothing. `asset_hash` is deliberately left: it is the
    record that this revision WAS rendered, which is still true, and it is
    what keeps the one-render-per-revision guard honest."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        key = f'spotlight/{rk}/1-abc-facebook.png'
        attach_render(tx, current_revision(tx, rk)['id'], 'h', key, 'https://cdn/x.png')
        tx.execute(
            """UPDATE publishing_queue
                  SET status = 'published', external_post_id = '1',
                      image_key = 'spotlight/' || request_key || '/1-abc-' || platform || '.png',
                      image_url = 'https://cdn/x.png', updated_at = NOW() - interval '91 days'
                WHERE request_key = %(rk)s""", dict(rk=rk))
        retention_sweep(tx)
        rev = current_revision(tx, rk)
        assert rev['image_key'] == key and rev['asset_hash'] == 'h'
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: list(keys),   # storage confirms everything
                                    target_prefix=f'spotlight/{rk}/')
    assert out['done'] == 2
    with api_tx('read committed') as tx:
        rev = current_revision(tx, rk)
        assert rev['image_key'] is None and rev['image_url'] is None
        assert rev['asset_hash'] == 'h'
        state = card_state(tx, rk)
    assert state['image_url'] is None
    # The revision still says it was rendered, so the card screen reports the
    # preview as having existed rather than pretending the render never
    # happened.
    assert state['preview_available'] is True


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


def test_removals_rows_carry_their_retry_and_deadline_state(client, make_person):
    """Fix wave item 8. The removals list is what an operator works from, and
    it was answering with the task's identity only: no attempt count, no last
    error, no next attempt time, no deadline, no evidence. A task that had
    been failing for two days looked exactly like one filed a minute ago, and
    the `overdue` counter said how many were late without saying which. All
    five columns already exist on the row, so this is a projection fix."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'published', external_post_id = '9' "
                   "WHERE request_key = %(rk)s", dict(rk=rk))
        from service.spotlight.withdrawal import withdraw_member
        withdraw_member(tx, p['id'], 'opt_out')
        tx.execute("""UPDATE spotlight_removal_task
                         SET attempts = 3, last_error = 'graph api said no'
                       WHERE request_key = %(rk)s""", dict(rk=rk))
    body = client.get('/admin/growth/removals?pending=1', headers=H).get_json()
    mine = [t for t in body['tasks'] if t['request_key'] == rk]
    assert mine
    for task in mine:
        assert task['attempts'] == 3
        assert task['last_error'] == 'graph api said no'
        # A deadline is stamped at filing time, and the next attempt time is
        # what says whether the worker is backing off.
        assert task['next_attempt_at'] and task['deadline_at']
        assert 'evidence' in task


def test_abandoned_jobs_are_counted_on_the_removals_surface(client):
    """Fix wave item 3. An abandoned job is deliberately left in the table and
    nothing sweeps it up, so the only thing that can make it visible is the
    operator surface. Without a count here the retention sweep's new guard
    (which stops re-queueing the key) would make an abandoned job silent as
    well as unswept: an object still in the bucket that nobody is told about.
    Counted even under the emergency stop, like `overdue` and
    `outstanding_cleanup`, since a stop is exactly when a backlog must stay
    visible."""
    key = _pfx() + 'abandoned-facebook.png'
    before = client.get('/admin/growth/removals?pending=1', headers=H).get_json()
    assert 'abandoned_cleanup' in before
    with api_tx() as tx:
        tx.execute("""INSERT INTO cleanup_job (kind, target, state, last_error)
                      VALUES ('asset_delete', %(k)s, 'abandoned', 'deletion not confirmed by storage')""",
                   dict(k=key))
    after = client.get('/admin/growth/removals?pending=1', headers=H).get_json()
    assert after['abandoned_cleanup'] == before['abandoned_cleanup'] + 1


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
        assert attach_platform_image(tx, rk, 'facebook', rev2, key2, 'https://cdn/fb2.png', sha2)[0] == 'attached'
        assert complete_render_if_ready(tx, rk, rev2) is False

    rows = _jobs(old['facebook'], old['instagram'])
    assert set(rows) == {old['facebook'], old['instagram']}
    assert {r['state'] for r in rows.values()} == {'pending'}


def test_caption_edit_spares_a_row_whose_delivery_is_unresolved(make_person):
    """Fix round 1, ruling 3. A row parked in `review` with an unresolved
    delivery may have a LIVE post behind it. Un-rendering it (and queueing its
    artwork for deletion) would destroy the card that post shows, so the
    re-point scope skips it: its image columns survive the caption edit and
    nothing is queued for its key. The sibling that is genuinely un-rendered
    is still un-rendered."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        live = f'spotlight/{rk}/live-facebook.png'
        pending = f'spotlight/{rk}/pending-instagram.png'
        tx.execute("""UPDATE publishing_queue
                         SET status = 'review', delivery_state = 'delivery_unknown',
                             image_key = %(k)s, image_url = 'https://cdn/live.png', image_sha256 = 'livesha'
                       WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(rk=rk, k=live))
        tx.execute("""UPDATE publishing_queue
                         SET status = 'awaiting_render', image_key = %(k)s,
                             image_url = 'https://cdn/pending.png', image_sha256 = 'pendingsha'
                       WHERE request_key = %(rk)s AND platform = 'instagram'""", dict(rk=rk, k=pending))
        edit_caption(tx, rk, 'a different caption', 't')
        rows = {r['platform']: r for r in tx.execute(
            "SELECT platform, image_key, image_url, image_sha256 FROM publishing_queue WHERE request_key = %(rk)s",
            dict(rk=rk)).fetchall()}
    assert rows['facebook']['image_key'] == live
    assert rows['facebook']['image_url'] == 'https://cdn/live.png'
    assert rows['facebook']['image_sha256'] == 'livesha'
    assert rows['instagram']['image_key'] is None and rows['instagram']['image_sha256'] is None
    jobs = _jobs(live, pending)
    assert live not in jobs
    assert jobs[pending]['state'] == 'pending'
