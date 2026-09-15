"""The single Spotlight withdrawal operation (Wave 1 remediation, F03) and
every lifecycle exit wired through it: opt-out, self-delete, admin ban, and
the pending-deletion cron. `_make_eligible` and `_photo` are copied from
tests/test_spotlight_queue.py / tests/test_spotlight_revisions.py on purpose
-- test files in this suite do not import from each other. `_auth_headers_for`
mirrors `_session_for` in tests/test_spotlight_routes.py."""
import asyncio
import hashlib
import secrets

import pytest

from database import api_tx
from service.spotlight.queue import create_candidate
from service.spotlight.revisions import current_revision, attach_render, record_consent, create_revision
from service.spotlight.withdrawal import withdraw_member
from service.spotlight import set_spotlight_opt_in
from emails.send_community_weekly import _week_context


def _make_eligible(make_person, name='Elig', gender='Woman'):
    p = make_person(name=name, gender=gender)
    with api_tx() as tx:
        tx.execute("""
            UPDATE person SET spotlight_opt_in = TRUE, spotlight_opt_in_at = NOW(),
                   ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01',
                   deletion_requested_at = NULL, spotlight_last_featured_at = NULL
             WHERE id = %(id)s""", dict(id=p['id']))
        # photo has NOT NULL blurhash and hash columns with no default (checked \d photo);
        # uuid is a text column (not native uuid type) but gen_random_uuid() casts in fine.
        tx.execute("""
            INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
            VALUES (gen_random_uuid(), %(id)s, 1, 'approved', 'testblurhash', gen_random_uuid()::text)""", dict(id=p['id']))
    return p


def _photo(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s ORDER BY position LIMIT 1", dict(id=pid)).fetchone()['u']


def _auth_headers_for(p, signed_in: bool = True) -> dict:
    """Mint a real duo_session row and return the bearer auth header. Copied
    from `_session_for` in tests/test_spotlight_routes.py."""
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(i)s", dict(i=p['id'])).fetchone()['email']
        tx.execute(
            """INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
               VALUES (%(h)s, %(e)s, %(p)s, %(s)s, '123456')""",
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=p['id'], s=signed_in))
    return {'Authorization': f'Bearer {tok}'}


def _make_admin(make_person):
    p = make_person(name='WithdrawAdmin')
    with api_tx() as tx:
        tx.execute("UPDATE person SET roles = ARRAY['admin']::TEXT[] WHERE id = %(i)s", dict(i=p['id']))
    return p


def _mint_ban_token(tx, person_id: int) -> str:
    """No production mint function exists outside the report-and-ban flow
    (antiabuse.sql.Q_MAKE_REPORT); build the token row directly the way
    Q_ADMIN_BAN (service/person/sql/__init__.py) reads it."""
    return tx.execute(
        "INSERT INTO banned_person_admin_token (person_id) VALUES (%(pid)s) RETURNING token::text AS token",
        dict(pid=person_id)).fetchone()['token']


def _row_in(tx, pid, status, *, kind='welcome', delivery_state='none', external=None):
    """One request in the given status, both platforms. Returns request_key."""
    rk = create_candidate(tx, kind=kind, subject_person_id=pid if kind != 'roundup' else None, caption='c', created_by='t')
    tx.execute("""UPDATE publishing_queue SET status = %(st)s, delivery_state = %(ds)s, external_post_id = %(ext)s,
                         image_key = 'spotlight/' || request_key || '-' || platform || '.png', updated_at = NOW()
                   WHERE request_key = %(rk)s""", dict(st=status, ds=delivery_state, ext=external, rk=rk))
    return rk


def _statuses(tx, rk):
    return {r['platform']: (r['status'], r['cancellation_requested_at'] is not None) for r in
            tx.execute("SELECT platform, status, cancellation_requested_at FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()}


@pytest.mark.parametrize('status,expected_status,stamped', [
    ('awaiting_member', 'cancelled', True),
    ('review', 'cancelled', True),
    ('scheduled', 'cancelled', True),
    ('failed', 'cancelled', True),
    ('processing', 'processing', True),
    ('published', 'published', True),
])
def test_every_status_is_handled(make_person, status, expected_status, stamped):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = _row_in(tx, p['id'], status, delivery_state='attempting' if status == 'processing' else 'none',
                     external='123' if status == 'published' else None)
        out = withdraw_member(tx, p['id'], 'opt_out')
        assert _statuses(tx, rk) == {'facebook': (expected_status, stamped), 'instagram': (expected_status, stamped)}
        if status == 'published':
            tasks = tx.execute("SELECT person_id, request_key, platform FROM spotlight_removal_task WHERE request_key = %(rk)s ORDER BY platform", dict(rk=rk)).fetchall()
            assert [(t['person_id'], t['request_key']) for t in tasks] == [(p['id'], rk)] * 2
            assert out['removal_tasks'] == 2
        if status == 'processing':
            assert out['left_attempting'] == 2 and out['cancelled'] == 0
        if expected_status == 'cancelled':
            assert out['cancelled'] == 2
        assert out['epoch'] == 1


def test_second_call_is_idempotent_except_epoch(make_person):
    """Fix round 1 (ruling 2): a processing row's cancellation stamp is
    idempotent too -- `left_attempting` must count 2 on the first call and
    0 on the second, the same idempotence `cancelled` already had."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        _row_in(tx, p['id'], 'review')
        _row_in(tx, p['id'], 'processing', delivery_state='attempting')
        first = withdraw_member(tx, p['id'], 'opt_out')
        second = withdraw_member(tx, p['id'], 'opt_out')
        assert first['cancelled'] == 2
        assert first['left_attempting'] == 2
        assert {k: v for k, v in second.items() if k != 'epoch'} == dict(cancelled=0, left_attempting=0, roundups_reissued=0, removal_tasks=0, nonces_invalidated=0)
        assert second['epoch'] == 2


def test_bad_reason_rejected(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        with pytest.raises(ValueError, match='bad_reason'):
            withdraw_member(tx, p['id'], 'because')


def test_withdraw_member_requires_existing_person(make_person):
    """Fix round 1 (ruling 4): `withdraw_member` must refuse a person_id
    with no person row, before any write -- there is nothing to snapshot a
    removal task's person_id against, nothing to bump the consent epoch on."""
    p = make_person()
    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %(p)s", dict(p=p['id']))
    with api_tx() as tx:
        with pytest.raises(ValueError, match='not_found'):
            withdraw_member(tx, p['id'], 'opt_out')


def test_nonces_invalidated(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("INSERT INTO spotlight_token_nonce (nonce, person_id, purpose, epoch) VALUES ('n-live', %(p)s, 'card', 0), ('n-used', %(p)s, 'confirm', 0)", dict(p=p['id']))
        tx.execute("UPDATE spotlight_token_nonce SET used_at = NOW() - interval '1 day' WHERE nonce = 'n-used'")
        out = withdraw_member(tx, p['id'], 'opt_out')
        assert out['nonces_invalidated'] == 1
        assert tx.execute("SELECT count(*) AS n FROM spotlight_token_nonce WHERE person_id = %(p)s AND used_at IS NULL", dict(p=p['id'])).fetchone()['n'] == 0


def test_roundup_participant_reissued_without_member(make_person):
    a = _make_eligible(make_person, name='A'); b = _make_eligible(make_person, name='B', gender='Man')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        rev = current_revision(tx, rk)
        create_revision(tx, rk, caption='r', photo_uuid=None, participants=[dict(person_id=a['id']), dict(person_id=b['id'])], channels=rev['channels'], created_by='t')
        rev2 = current_revision(tx, rk)
        attach_render(tx, rev2['id'], 'h', 'k', 'https://cdn/k.png')
        tx.execute("UPDATE publishing_queue SET status = 'review', image_key = 'k', image_url = 'https://cdn/k.png' WHERE request_key = %(rk)s", dict(rk=rk))
        record_consent(tx, rev2['id'], a['id'], 'participant'); record_consent(tx, rev2['id'], b['id'], 'participant')
        out = withdraw_member(tx, a['id'], 'account_deletion')
        assert out['roundups_reissued'] == 1
        rev3 = current_revision(tx, rk)
        assert rev3['id'] != rev2['id'] and [x['person_id'] for x in rev3['participants']] == [b['id']]
        assert rev3['asset_hash'] is None
        assert tx.execute("SELECT count(*) AS n FROM spotlight_revision_consent WHERE revision_id = %(r)s", dict(r=rev3['id'])).fetchone()['n'] == 0
        assert {r['status'] for r in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'awaiting_render'}


def test_reissue_leaves_a_published_sibling_row_untouched(make_person):
    """Fix round 1 (ruling 1): Task 5's per-platform `record_receipt` means
    a roundup's two platform rows can now complete independently -- one can
    already be `published` while the other is still `review`. The re-issue
    step must only touch the still-pending row: the published sibling keeps
    its own status, external_post_id and image_key (it was already handled
    by step 2's removal-task filing), and re-issuing the pending row must
    not raise despite the published sibling (`create_revision`'s terminal
    guard, `ignore_terminal_siblings=True`).

    Fix round 2 (ruling 2): the published row must also keep pointing at
    the revision it was actually published with -- `create_revision`'s
    current_revision_id repoint is scoped away from published/cancelled
    rows when `ignore_terminal_siblings=True` -- while the re-issued row
    points at the brand new revision."""
    a = _make_eligible(make_person, name='A')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        rev = current_revision(tx, rk)
        create_revision(tx, rk, caption='r', photo_uuid=None, participants=[dict(person_id=a['id'])], channels=rev['channels'], created_by='t')
        rev2 = current_revision(tx, rk)
        tx.execute("""UPDATE publishing_queue SET status = 'published', external_post_id = 'fb-1',
                             image_key = 'spotlight/live.png', image_url = 'https://cdn/live.png', delivery_state = 'published'
                       WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(rk=rk))
        tx.execute("""UPDATE publishing_queue SET status = 'review', image_key = 'spotlight/pending.png',
                             image_url = 'https://cdn/pending.png'
                       WHERE request_key = %(rk)s AND platform = 'instagram'""", dict(rk=rk))
        out = withdraw_member(tx, a['id'], 'account_deletion')
        assert out['roundups_reissued'] == 1
        assert out['cancelled'] == 0
        rows = {r['platform']: r for r in tx.execute(
            "SELECT platform, status, external_post_id, image_key, current_revision_id FROM publishing_queue WHERE request_key = %(rk)s",
            dict(rk=rk)).fetchall()}
        assert rows['facebook']['status'] == 'published'
        assert rows['facebook']['external_post_id'] == 'fb-1'
        assert rows['facebook']['image_key'] == 'spotlight/live.png'
        assert rows['facebook']['current_revision_id'] == rev2['id']
        assert rows['instagram']['status'] == 'awaiting_render'
        assert rows['instagram']['image_key'] is None
        assert rows['instagram']['current_revision_id'] != rev2['id']
        tasks = tx.execute("SELECT platform, external_post_id FROM spotlight_removal_task WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()
        assert [(t['platform'], t['external_post_id']) for t in tasks] == [('facebook', 'fb-1')]


def test_reissue_clears_sha256_so_a_partial_reupload_does_not_complete(make_person):
    """Fix round 1 (ruling 2): image_sha256 is cleared everywhere image_key
    is, including this re-issue reset -- otherwise a stale image_sha256
    surviving the reset could let a platform that was never re-rendered
    (its image_key genuinely NULL, but a leftover hash from the old
    revision) silently look complete once the other platform's fresh
    upload lands. Re-issuing after a full render, then uploading only
    facebook, must leave the set NOT ready: instagram stays
    awaiting_render with no image_sha256, and the revision's asset_hash
    stays NULL."""
    from service.spotlight.assets import asset_key, attach_platform_image, complete_render_if_ready
    a = _make_eligible(make_person, name='A'); b = _make_eligible(make_person, name='B', gender='Man')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        rev = current_revision(tx, rk)
        create_revision(tx, rk, caption='r', photo_uuid=None,
                        participants=[dict(person_id=a['id']), dict(person_id=b['id'])],
                        channels=rev['channels'], created_by='t')
        rev2 = current_revision(tx, rk)
        fb_key = asset_key(rk, rev2['id'], 'oldsha', 'facebook')
        ig_key = asset_key(rk, rev2['id'], 'oldsha', 'instagram')
        assert attach_platform_image(tx, rk, 'facebook', rev2['id'], fb_key, 'https://cdn/fb.png', 'oldsha') == 'attached'
        assert attach_platform_image(tx, rk, 'instagram', rev2['id'], ig_key, 'https://cdn/ig.png', 'oldsha') == 'attached'
        assert complete_render_if_ready(tx, rk, rev2['id']) is True

        withdraw_member(tx, a['id'], 'account_deletion')
        new_rev = current_revision(tx, rk)
        assert new_rev['id'] != rev2['id'] and new_rev['asset_hash'] is None
        rows = {r['platform']: r for r in tx.execute(
            "SELECT platform, image_key, image_sha256 FROM publishing_queue WHERE request_key = %(rk)s",
            dict(rk=rk)).fetchall()}
        assert rows['facebook']['image_key'] is None and rows['facebook']['image_sha256'] is None
        assert rows['instagram']['image_key'] is None and rows['instagram']['image_sha256'] is None

        # Upload facebook only -- the set must not read as ready.
        fb_key2 = asset_key(rk, new_rev['id'], 'newsha', 'facebook')
        assert attach_platform_image(tx, rk, 'facebook', new_rev['id'], fb_key2, 'https://cdn/fb2.png', 'newsha') == 'attached'
        assert complete_render_if_ready(tx, rk, new_rev['id']) is False
        rows2 = {r['platform']: r for r in tx.execute(
            "SELECT platform, status, image_sha256 FROM publishing_queue WHERE request_key = %(rk)s",
            dict(rk=rk)).fetchall()}
        assert rows2['instagram']['status'] == 'awaiting_render' and rows2['instagram']['image_sha256'] is None
        assert current_revision(tx, rk)['asset_hash'] is None


def test_reissued_row_completes_render_ignoring_published_sibling(make_person):
    """Fix round 1 (ruling 3): complete_render_if_ready only considers rows
    still in an uploadable status (awaiting_member/awaiting_render/review).
    Continues test_reissue_leaves_a_published_sibling_row_untouched's setup:
    facebook is published on the OLD revision, instagram was re-issued onto
    a brand new one. Uploading instagram must complete the set and attach
    the render to the NEW revision, without the published facebook sibling
    (a different revision entirely) blocking it or being pinned into it."""
    from service.spotlight.assets import asset_key, attach_platform_image, complete_render_if_ready
    a = _make_eligible(make_person, name='A')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        rev = current_revision(tx, rk)
        create_revision(tx, rk, caption='r', photo_uuid=None, participants=[dict(person_id=a['id'])], channels=rev['channels'], created_by='t')
        rev2 = current_revision(tx, rk)
        tx.execute("""UPDATE publishing_queue SET status = 'published', external_post_id = 'fb-1',
                             image_key = 'spotlight/live.png', image_url = 'https://cdn/live.png', delivery_state = 'published'
                       WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(rk=rk))
        tx.execute("""UPDATE publishing_queue SET status = 'review', image_key = 'spotlight/pending.png',
                             image_url = 'https://cdn/pending.png'
                       WHERE request_key = %(rk)s AND platform = 'instagram'""", dict(rk=rk))
        withdraw_member(tx, a['id'], 'account_deletion')
        # current_revision(tx, rk) is ambiguous once the two platform rows
        # point at DIFFERENT revisions on purpose (the published sibling
        # keeps its own, older one) -- it has no ORDER BY and either row
        # could win, so the instagram row's own current_revision_id is read
        # directly instead.
        ig_row = tx.execute(
            "SELECT current_revision_id FROM publishing_queue WHERE request_key = %(rk)s AND platform = 'instagram'",
            dict(rk=rk)).fetchone()
        new_rev_id = ig_row['current_revision_id']
        assert new_rev_id != rev2['id']

        ig_key = asset_key(rk, new_rev_id, 'igsha', 'instagram')
        assert attach_platform_image(tx, rk, 'instagram', new_rev_id, ig_key, 'https://cdn/ig.png', 'igsha') == 'attached'
        assert complete_render_if_ready(tx, rk, new_rev_id) is True

        attached = tx.execute("SELECT asset_hash, image_key FROM spotlight_revision WHERE id = %(id)s",
                              dict(id=new_rev_id)).fetchone()
        assert attached['asset_hash'] == 'igsha' and attached['image_key'] == ig_key
        fb = tx.execute("SELECT status, image_key, current_revision_id FROM publishing_queue WHERE request_key = %(rk)s AND platform = 'facebook'",
                        dict(rk=rk)).fetchone()
        assert fb['status'] == 'published' and fb['image_key'] == 'spotlight/live.png'
        assert fb['current_revision_id'] == rev2['id']


def test_published_roundup_participant_files_removal_task(make_person):
    a = _make_eligible(make_person, name='A')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        rev = current_revision(tx, rk)
        create_revision(tx, rk, caption='r', photo_uuid=None, participants=[dict(person_id=a['id'])], channels=rev['channels'], created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'published', external_post_id = '9', delivery_state = 'published' WHERE request_key = %(rk)s", dict(rk=rk))
        out = withdraw_member(tx, a['id'], 'ban')
        assert out['removal_tasks'] == 2
        assert tx.execute("SELECT count(*) AS n FROM spotlight_removal_task WHERE request_key = %(rk)s AND person_id = %(p)s", dict(rk=rk, p=a['id'])).fetchone()['n'] == 2


def test_removal_task_survives_person_delete(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = _row_in(tx, p['id'], 'published', external='55')
        withdraw_member(tx, p['id'], 'hard_delete')
    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %(p)s", dict(p=p['id']))
    with api_tx() as tx:
        tasks = tx.execute("SELECT person_id, request_key, external_post_id, done_at FROM spotlight_removal_task WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()
        assert len(tasks) == 2 and all(t['person_id'] == p['id'] and t['external_post_id'] == '55' and t['done_at'] is None for t in tasks)
        assert tx.execute("SELECT count(*) AS n FROM publishing_queue WHERE request_key = %(rk)s AND subject_person_id IS NULL", dict(rk=rk)).fetchone()['n'] == 2


# Entry points

def test_opt_out_withdraws(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = _row_in(tx, p['id'], 'scheduled')
        set_spotlight_opt_in(tx, p['id'], False)
        assert {v[0] for v in _statuses(tx, rk).values()} == {'cancelled'}
        assert tx.execute("SELECT spotlight_consent_epoch AS e FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['e'] == 1


def test_self_delete_route_withdraws(make_person, client):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = _row_in(tx, p['id'], 'review')
    resp = client.delete('/account', headers=_auth_headers_for(p))
    assert resp.status_code == 200
    with api_tx() as tx:
        assert {v[0] for v in _statuses(tx, rk).values()} == {'cancelled'}
        row = tx.execute("SELECT deletion_requested_at, spotlight_consent_epoch AS e FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()
        assert row['deletion_requested_at'] is not None and row['e'] == 1


def test_admin_ban_withdraws_before_delete(make_person):
    from service.person import delete_or_ban_account
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = _row_in(tx, p['id'], 'published', external='77')
        token = _mint_ban_token(tx, p['id'])
    delete_or_ban_account(s=None, admin_ban_token=token)
    with api_tx() as tx:
        assert tx.execute("SELECT count(*) AS n FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['n'] == 0
        assert tx.execute("SELECT count(*) AS n FROM spotlight_removal_task WHERE request_key = %(rk)s AND person_id = %(p)s AND external_post_id = '77'", dict(rk=rk, p=p['id'])).fetchone()['n'] == 2


def test_pending_deletion_cron_withdraws_before_hard_delete(make_person):
    from service.cron.pendingdeletion import hard_delete_expired_once
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = _row_in(tx, p['id'], 'published', external='88')
        # sign_in_time is NOT NULL on this schema (unlike the brief's literal
        # snippet, which sets it to NULL) and defaults to NOW() at insert, so
        # it must be pushed earlier than deletion_requested_at explicitly to
        # satisfy the cron's `sign_in_time < deletion_requested_at` check.
        tx.execute("""UPDATE person SET activated = FALSE, deletion_requested_at = NOW() - interval '8 days',
                             sign_in_time = NOW() - interval '9 days' WHERE id = %(p)s""", dict(p=p['id']))
    asyncio.run(hard_delete_expired_once())
    with api_tx() as tx:
        assert tx.execute("SELECT count(*) AS n FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['n'] == 0
        assert tx.execute("SELECT count(*) AS n FROM spotlight_removal_task WHERE request_key = %(rk)s AND person_id = %(p)s AND external_post_id = '88'", dict(rk=rk, p=p['id'])).fetchone()['n'] == 2


def test_cron_second_pass_excludes_a_person_not_covered_by_the_withdrawal_pass(make_person, monkeypatch):
    """Fix round 1 (ruling 3): `_withdraw_all` returns exactly the ids it
    withdrew, and the hard-delete pass intersects against that list. Proven
    by simulating a second member crossing the grace boundary DURING the
    withdrawal pass (a real race the two-pass split could otherwise miss):
    that member must survive this run even though a naive re-select in pass
    2 would find them freshly expired."""
    import service.cron.pendingdeletion as pd
    from service.cron.pendingdeletion import hard_delete_expired_once
    p = _make_eligible(make_person)
    latecomer = make_person(name='Latecomer')
    with api_tx() as tx:
        tx.execute("""UPDATE person SET activated = FALSE, deletion_requested_at = NOW() - interval '8 days',
                             sign_in_time = NOW() - interval '9 days' WHERE id = %(p)s""", dict(p=p['id']))
    real_withdraw_all = pd._withdraw_all

    def _fake_withdraw_all(person_ids):
        assert latecomer['id'] not in person_ids  # sanity: the race hasn't happened yet from pass 1's view
        with api_tx() as tx:
            tx.execute("""UPDATE person SET activated = FALSE, deletion_requested_at = NOW() - interval '8 days',
                                 sign_in_time = NOW() - interval '9 days' WHERE id = %(p)s""", dict(p=latecomer['id']))
        return real_withdraw_all(person_ids)

    monkeypatch.setattr(pd, '_withdraw_all', _fake_withdraw_all)
    asyncio.run(hard_delete_expired_once())
    with api_tx() as tx:
        assert tx.execute("SELECT count(*) AS n FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['n'] == 0
        assert tx.execute("SELECT count(*) AS n FROM person WHERE id = %(p)s", dict(p=latecomer['id'])).fetchone()['n'] == 1


def test_admin_deactivate_route_withdraws(make_person, client):
    """Fix round 1 (ruling 5): POST /admin/users/:uuid/deactivate must
    withdraw Spotlight in the same transaction as the deactivation."""
    admin = _make_admin(make_person)
    headers = _auth_headers_for(admin)
    p = _make_eligible(make_person)
    with api_tx() as tx:
        target_uuid = tx.execute("SELECT uuid::text AS u FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['u']
        rk = _row_in(tx, p['id'], 'review')
    resp = client.post(f'/admin/users/{target_uuid}/deactivate', json={'reason': 'policy violation'}, headers=headers)
    assert resp.status_code == 200
    with api_tx() as tx:
        assert {v[0] for v in _statuses(tx, rk).values()} == {'cancelled'}
        assert tx.execute("SELECT spotlight_consent_epoch AS e FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['e'] == 1


def test_admin_hard_delete_route_withdraws(make_person, client):
    """Fix round 1 (ruling 5): DELETE /admin/users/:uuid must withdraw
    Spotlight before the hard-delete -- the removal task's snapshotted
    person_id/request_key must survive the person row disappearing."""
    admin = _make_admin(make_person)
    headers = _auth_headers_for(admin)
    p = _make_eligible(make_person)
    with api_tx() as tx:
        row = tx.execute("SELECT uuid::text AS u, email FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()
        target_uuid, email = row['u'], row['email']
        rk = _row_in(tx, p['id'], 'published', external='66')
    resp = client.delete(f'/admin/users/{target_uuid}', json={'confirm_email': email, 'reason': 'policy violation'}, headers=headers)
    assert resp.status_code == 200
    with api_tx() as tx:
        assert tx.execute("SELECT count(*) AS n FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['n'] == 0
        assert tx.execute(
            "SELECT count(*) AS n FROM spotlight_removal_task WHERE request_key = %(rk)s AND person_id = %(p)s AND external_post_id = '66'",
            dict(rk=rk, p=p['id'])).fetchone()['n'] == 2


def test_weekly_email_excludes_deleting_member(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = _row_in(tx, p['id'], 'published', kind='member_of_week', external='99')
        tx.execute("UPDATE publishing_queue SET image_url = 'https://cdn/x.png' WHERE request_key = %(rk)s", dict(rk=rk))
    before = _week_context()['spotlight']
    assert before is not None and before['post_url'].endswith('99')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = FALSE, deletion_requested_at = NOW() WHERE id = %(p)s", dict(p=p['id']))
    assert _week_context()['spotlight'] is None


def test_review_row_with_an_unresolved_delivery_is_never_cancelled(make_person):
    """Fix wave item 1: a row parked in `review` with delivery_state
    `attempting` (a reaped lease) or `delivery_unknown` may have a LIVE post
    behind it. Cancelling it, and deleting its artwork, would orphan that
    post: nothing would ever file a removal task for it. It is stamped and
    handed to a human through an `investigate` task instead."""
    for delivery_state in ('attempting', 'delivery_unknown'):
        p = _make_eligible(make_person, name=f'InFlight{delivery_state}')
        with api_tx() as tx:
            rk = _row_in(tx, p['id'], 'review', delivery_state=delivery_state)
            out = withdraw_member(tx, p['id'], 'opt_out')
            assert out['cancelled'] == 0
            assert out['left_attempting'] == 2
            assert out['removal_tasks'] == 2
            assert _statuses(tx, rk) == {'facebook': ('review', True), 'instagram': ('review', True)}
            rows = tx.execute(
                "SELECT image_key FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()
            assert all(r['image_key'] is not None for r in rows)
            tasks = tx.execute(
                """SELECT reason, external_post_id, person_id FROM spotlight_removal_task
                    WHERE request_key = %(rk)s ORDER BY platform""", dict(rk=rk)).fetchall()
            assert [(t['reason'], t['external_post_id'], t['person_id']) for t in tasks] == [
                ('investigate', None, p['id'])] * 2


def test_investigate_task_is_listed_for_the_operator(make_person, client):
    """The removals surface lists an `investigate` task like any other; the
    admin worker leaves every reason other than `delete_via_api` alone."""
    admin = _make_admin(make_person)
    p = _make_eligible(make_person, name='Investigate')
    with api_tx() as tx:
        rk = _row_in(tx, p['id'], 'review', delivery_state='attempting')
        withdraw_member(tx, p['id'], 'opt_out')
    listed = client.get('/admin/growth/removals?pending=1', headers=_auth_headers_for(admin)).get_json()
    mine = [t for t in listed['tasks'] if t['request_key'] == rk]
    assert len(mine) == 2 and {t['reason'] for t in mine} == {'investigate'}


def test_withdrawal_clears_the_standing_preference(make_person, client):
    """Fix wave item 7: withdrawal is a consent event, so the standing
    `spotlight_opt_in` flag goes with it. Reactivating the account does not
    bring it back -- the member has to opt in again -- so they stay out of
    both the welcome cohort and the suggest list."""
    from service.api.admin.spotlight_routes import _Q_WELCOME_CANDIDATES, _Q_SUGGEST
    admin = _make_admin(make_person)
    headers = _auth_headers_for(admin)
    p = _make_eligible(make_person, name='StandingPref')
    with api_tx() as tx:
        target_uuid = tx.execute("SELECT uuid::text AS u FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['u']
        welcome_before = {r['id'] for r in tx.execute(_Q_WELCOME_CANDIDATES).fetchall()}
        assert p['id'] in welcome_before
    assert client.post(f'/admin/users/{target_uuid}/deactivate', json={'reason': 'policy violation'},
                       headers=headers).status_code == 200
    assert client.post(f'/admin/users/{target_uuid}/reactivate', json={'reason': 'appeal upheld'},
                       headers=headers).status_code == 200
    with api_tx() as tx:
        assert tx.execute("SELECT spotlight_opt_in AS o, activated AS a FROM person WHERE id = %(p)s",
                          dict(p=p['id'])).fetchone() == dict(o=False, a=True)
        assert p['id'] not in {r['id'] for r in tx.execute(_Q_WELCOME_CANDIDATES).fetchall()}
        assert p['id'] not in {r['id'] for r in tx.execute(_Q_SUGGEST, dict(recent=None)).fetchall()}
        # Opting in again is all it takes to be a candidate once more.
        set_spotlight_opt_in(tx, p['id'], True)
        assert p['id'] in {r['id'] for r in tx.execute(_Q_SUGGEST, dict(recent=None)).fetchall()}
