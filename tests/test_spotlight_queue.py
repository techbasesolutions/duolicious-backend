import json

import pytest
from database import api_tx
from service.spotlight.queue import (create_candidate, expire_member_approvals,
                                     set_status, cancel_for_member, settings, set_setting)
from service.spotlight.revisions import current_revision, attach_render, approve_card
from service.spotlight import set_spotlight_opt_in


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


def _rows(tx, rk):
    return tx.execute("SELECT * FROM publishing_queue WHERE request_key = %(rk)s ORDER BY platform", dict(rk=rk)).fetchall()


def test_create_candidate_two_rows_awaiting_member(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='Welcome', created_by='test')
        rows = _rows(tx, rk)
    assert [r['platform'] for r in rows] == ['facebook', 'instagram']
    assert {r['status'] for r in rows} == {'awaiting_member'}


def test_create_candidate_refuses_ineligible(make_person):
    p = make_person(name='NoOpt')
    with api_tx() as tx:
        with pytest.raises(ValueError) as e:
            create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='x', created_by='test')
        assert 'not_opted_in' in str(e.value) or 'not_verified' in str(e.value)


def test_roundup_has_no_subject_and_awaits_render():
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='New this week', created_by='tick')
        assert {r['status'] for r in _rows(tx, rk)} == {'awaiting_render'}


def test_member_approval_then_render_then_review(make_person):
    """set_member_approval + attach_image (pre-Wave-1 queue.py) are replaced
    by revision-bound consent: the card is rendered first (attach_render),
    then approve_card records the subject's consent against that exact
    revision and, once consent is complete, moves the row on to review."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
            attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/x.png')
            assert {r['status'] for r in _rows(tx, rk)} == {'awaiting_member'}
            assert approve_card(tx, rk, p['id'], photo, shown_revision=1) == 'approved'
            assert {r['status'] for r in _rows(tx, rk)} == {'review'}
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_approve_card_rejects_foreign_photo(make_person):
    p = _make_eligible(make_person)
    other = _make_eligible(make_person, name='Other')
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
            foreign = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=other['id'])).fetchone()['u']
            with pytest.raises(ValueError, match='photo_not_owned'):
                approve_card(tx, rk, p['id'], foreign, shown_revision=1)
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_expire_member_approvals(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET created_at = NOW() - interval '8 days' WHERE request_key = %(rk)s", dict(rk=rk))
        assert expire_member_approvals(tx, days=7) >= 2
        assert {r['status'] for r in _rows(tx, rk)} == {'cancelled'}


def test_set_status_transitions(make_person):
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        # attach_image (pre-Wave-1) used to make this transition; a roundup
        # is ready for review as soon as it is rendered (Task 2), which this
        # test does not otherwise exercise, so the status is set directly.
        tx.execute("UPDATE publishing_queue SET status = 'review' WHERE request_key = %(rk)s", dict(rk=rk))
        qid = _rows(tx, rk)[0]['id']
        set_status(tx, qid, 'scheduled')
        with pytest.raises(ValueError):
            set_status(tx, qid, 'published')            # scheduled -> published not allowed (must pass processing)
        # Wave 1 F04: `processing` is no longer a source key in TRANSITIONS, so
        # a row leaves it only through `record_receipt` (tests/test_spotlight_delivery.py)
        # or the lease-expiry sweep -- never through `set_status`. The failed
        # state this test needs is set directly, the same way the lease-expiry
        # sweep itself bypasses set_status for a system-driven move.
        tx.execute("UPDATE publishing_queue SET status = 'failed', attempts = 3, error = 'boom' WHERE id = %(id)s", dict(id=qid))
        with pytest.raises(ValueError):
            set_status(tx, qid, 'scheduled')            # attempts exhausted


def test_cancel_for_member_creates_removal_tasks(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'published', external_post_id = '123' WHERE request_key = %(rk)s AND platform = 'instagram'", dict(rk=rk))
        tx.execute("UPDATE publishing_queue SET status = 'scheduled' WHERE request_key = %(rk)s AND platform = 'facebook'", dict(rk=rk))
        n = cancel_for_member(tx, p['id'], 'opt_out')
        assert n == 1
        tasks = tx.execute("SELECT t.platform, t.reason FROM spotlight_removal_task t JOIN publishing_queue q ON q.id = t.queue_id WHERE q.request_key = %(rk)s", dict(rk=rk)).fetchall()
        assert [(t['platform'], t['reason']) for t in tasks] == [('instagram', 'manual_instagram')]


def test_cancel_for_member_queues_stored_images_for_cleanup(make_person):
    """Wave 2 Task 5 (F09): cancelling a member's rows no longer deletes
    anything inline -- `withdraw_member` (which `cancel_for_member` wraps,
    Wave 1 F03) enqueues an asset_delete job per cancelled key inside its own
    transaction and makes no outbound call at all. A published row's artwork
    is still live, so it is not queued; the cancelled row's is."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        pub_key = f'spotlight/{rk}/published.png'
        sched_key = f'spotlight/{rk}/scheduled.png'
        tx.execute("""UPDATE publishing_queue SET status = 'published', external_post_id = '123',
                             image_key = %(k)s
                       WHERE request_key = %(rk)s AND platform = 'instagram'""", dict(rk=rk, k=pub_key))
        tx.execute("""UPDATE publishing_queue SET status = 'scheduled', image_key = %(k)s
                       WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(rk=rk, k=sched_key))
        cancel_for_member(tx, p['id'], 'opt_out')
    with api_tx('read committed') as tx:
        queued = {r['target'] for r in tx.execute(
            """SELECT target FROM cleanup_job
                WHERE kind = 'asset_delete' AND target = ANY(%(t)s::text[])""",
            dict(t=[pub_key, sched_key])).fetchall()}
    assert queued == {sched_key}


def test_opt_out_cancels(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        set_spotlight_opt_in(tx, p['id'], False)
        assert {r['status'] for r in _rows(tx, rk)} == {'cancelled'}


def test_settings_roundtrip():
    with api_tx() as tx:
        assert settings(tx)['publication_enabled'] in ('true', 'false')
        set_setting(tx, 'publication_enabled', 'true')
        assert settings(tx)['publication_enabled'] == 'true'
        with pytest.raises(ValueError):
            set_setting(tx, 'nope', 'true')
        set_setting(tx, 'publication_enabled', 'false')


def test_create_candidate_appends_a_campaign_link_to_the_caption(make_person):
    """I3: every card's caption carries its own /s/ link, minted once per
    request in create_candidate so both platform rows share one key, and a
    hand-written caption gets one too.

    Fix wave I1: the two rows no longer share one caption STRING. They share
    the key, and each row's copy of the link carries that row's own `?p=`, so
    the click the Facebook post earns can be told apart from the click the
    Instagram post earns. The revision keeps the bare, platform-neutral form.
    """
    from service.campaigns import record_click
    from service.config import WEB_BASE_URL
    from service.growth.queries import post_stats

    base = f"{WEB_BASE_URL.rstrip('/')}/s/"
    p = _make_eligible(make_person, name='Linked')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'],
                              caption='Welcome to Ahavah, Linked.', created_by='t')
        by_platform = {r['platform']: r['caption'] for r in _rows(tx, rk)}
        assert set(by_platform) == {'facebook', 'instagram'}
        for platform, caption in by_platform.items():
            assert caption.startswith('Welcome to Ahavah, Linked. ')
            assert f" {base}" in caption
            assert caption.endswith(f"?p={platform}")
        # One key, two captions: the split is in the query string, not in a
        # second campaign link.
        keys = {c.rsplit('/', 1)[1].split('?', 1)[0] for c in by_platform.values()}
        assert len(keys) == 1
        key = keys.pop()
        link = tx.execute("SELECT kind FROM campaign_link WHERE key = %(k)s", dict(k=key)).fetchone()
        assert link['kind'] == f'post:{rk}'

        # The revision keeps the bare link, with no platform stamp: it is one
        # immutable snapshot every row of the request shares.
        rev_caption = current_revision(tx, rk)['caption']
        assert rev_caption.endswith(f"{base}{key}")

        record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        # A click with no `?p=` param at all still lands under 'unknown'.
        assert post_stats(tx, rk) == {
            'clicks': 1, 'signups': 0,
            'by_platform': {
                'facebook': {'clicks': 0, 'signups': 0},
                'instagram': {'clicks': 0, 'signups': 0},
                'unknown': {'clicks': 1, 'signups': 0},
            },
        }


def test_a_real_click_through_a_caption_link_lands_in_its_own_platform_bucket(client, make_person):
    """Fix wave I1, the end-to-end proof: take the Instagram row's caption
    exactly as it was written to the database, pull the URL out of it, GET
    that URL through the real `/s/<key>` route, and check where `post_stats`
    puts the click.

    This is the test the dimension never had. Before the fix the caption
    carried a bare link, the route saw no `?p=` param, `record_click` stored
    a null platform and every real click landed under 'unknown'. The
    assertion below is exact on both buckets, so a regression that stops
    emitting `?p=` fails here rather than quietly re-labelling the numbers.
    """
    import re
    from urllib.parse import urlsplit

    from service.config import WEB_BASE_URL
    from service.growth.queries import post_stats

    p = _make_eligible(make_person, name='Bucketed')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'],
                              caption='Meet Bucketed.', created_by='t')
        caption = {r['platform']: r['caption'] for r in _rows(tx, rk)}['instagram']

    # Parse the link out of the caption the way a reader's browser would,
    # rather than rebuilding it from the key we happen to know.
    url = re.search(re.escape(WEB_BASE_URL.rstrip('/')) + r'/s/\S+', caption).group(0)
    parts = urlsplit(url)
    r = client.get(f"{parts.path}?{parts.query}", headers={'User-Agent': 'Mozilla/5.0 (iPhone)'})
    assert r.status_code == 302

    with api_tx() as tx:
        stats = post_stats(tx, rk)
    assert stats['clicks'] == 1
    assert stats['by_platform']['instagram'] == {'clicks': 1, 'signups': 0}
    assert stats['by_platform']['unknown'] == {'clicks': 0, 'signups': 0}
    assert stats['by_platform']['facebook'] == {'clicks': 0, 'signups': 0}
    # The parts still sum to the whole.
    assert stats['clicks'] == sum(v['clicks'] for v in stats['by_platform'].values())


def _tile_payload(person_id, first_name):
    return json.dumps(dict(
        tiles=[dict(person_id=person_id, first_name=first_name, photo_url='https://cdn/t.jpg')],
        count=1, countries=1))


def test_opt_out_cancels_a_roundup_that_tiles_the_member(make_person):
    """C1: a roundup carries no subject_person_id, so the subject sweep cannot
    reach it -- but its stored tile snapshot shows this member's photo, so
    withdrawn consent has to cancel it all the same. Another member's roundup
    is left alone."""
    p = _make_eligible(make_person, name='Tiled')
    other = _make_eligible(make_person, name='Untiled')
    with api_tx() as tx:
        mine = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        theirs = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET payload = %(pl)s::jsonb WHERE request_key = %(rk)s",
                   dict(pl=_tile_payload(p['id'], 'Tiled'), rk=mine))
        tx.execute("UPDATE publishing_queue SET payload = %(pl)s::jsonb WHERE request_key = %(rk)s",
                   dict(pl=_tile_payload(other['id'], 'Untiled'), rk=theirs))
        set_spotlight_opt_in(tx, p['id'], False)
        cancelled = _rows(tx, mine)
        untouched = _rows(tx, theirs)
    assert {r['status'] for r in cancelled} == {'cancelled'}
    assert {r['error'] for r in cancelled} == {'tile_member_opt_out'}
    assert {r['status'] for r in untouched} == {'awaiting_render'}


def test_opt_out_leaves_a_published_roundup_for_the_removal_path(make_person):
    """Published rows are owned by the retention sweep or a removal task, not
    by cancel_for_member's status sweep, whichever way the member is on the
    card. The task itself is covered by the test below."""
    p = _make_eligible(make_person, name='TiledLive')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue SET payload = %(pl)s::jsonb, status = 'published'
                       WHERE request_key = %(rk)s""",
                   dict(pl=_tile_payload(p['id'], 'TiledLive'), rk=rk))
        set_spotlight_opt_in(tx, p['id'], False)
        rows = _rows(tx, rk)
    assert {r['status'] for r in rows} == {'published'}


def test_opt_out_files_removal_tasks_for_a_published_roundup(make_person):
    """C3: a live roundup that tiles this member shows their photo just as a
    live card of their own does, so it earns the same removal task, with the
    same two reason strings the admin worker already acts on. The row stays
    `published` until the platform post is actually gone."""
    p = _make_eligible(make_person, name='TiledPublishedTask')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue
                         SET payload = %(pl)s::jsonb, status = 'published',
                             external_post_id = 'post-' || platform
                       WHERE request_key = %(rk)s""",
                   dict(pl=_tile_payload(p['id'], 'TiledPublishedTask'), rk=rk))
        set_spotlight_opt_in(tx, p['id'], False)
        rows = _rows(tx, rk)
        tasks = tx.execute("""SELECT t.platform, t.external_post_id, t.reason
                                FROM spotlight_removal_task t
                                JOIN publishing_queue q ON q.id = t.queue_id
                               WHERE q.request_key = %(rk)s
                               ORDER BY t.platform""", dict(rk=rk)).fetchall()
    assert {r['status'] for r in rows} == {'published'}
    assert [(t['platform'], t['external_post_id'], t['reason']) for t in tasks] == [
        ('facebook', 'post-facebook', 'delete_via_api'),
        ('instagram', 'post-instagram', 'manual_instagram'),
    ]


def test_opt_out_does_not_duplicate_removal_tasks_across_tile_members(make_person):
    """Two members tiled in the same published roundup can each opt out on
    their own. Both match the same two published queue rows (one per
    platform) in `_Q_PUBLISHED_TILE_ROWS`, so the second opt-out must not
    file a second removal task for a post the first opt-out already filed
    one for."""
    p1 = _make_eligible(make_person, name='TileOne')
    p2 = _make_eligible(make_person, name='TileTwo')
    payload = json.dumps(dict(
        tiles=[dict(person_id=p1['id'], first_name='TileOne', photo_url='https://cdn/t1.jpg'),
               dict(person_id=p2['id'], first_name='TileTwo', photo_url='https://cdn/t2.jpg')],
        count=2, countries=1))
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue
                         SET payload = %(pl)s::jsonb, status = 'published',
                             external_post_id = 'post-' || platform
                       WHERE request_key = %(rk)s""",
                   dict(pl=payload, rk=rk))
        set_spotlight_opt_in(tx, p1['id'], False)
        set_spotlight_opt_in(tx, p2['id'], False)
        tasks = tx.execute("""SELECT t.platform, t.queue_id::text AS queue_id, t.done_at
                                FROM spotlight_removal_task t
                                JOIN publishing_queue q ON q.id = t.queue_id
                               WHERE q.request_key = %(rk)s
                               ORDER BY t.platform""", dict(rk=rk)).fetchall()
    # One open task per platform row -- not one per opt-out.
    assert [t['platform'] for t in tasks] == ['facebook', 'instagram']
    assert len({t['queue_id'] for t in tasks}) == 2
    assert all(t['done_at'] is None for t in tasks)


def test_create_candidate_never_builds_roundup_participants(make_person):
    """Task 8 fix round 1 (ruling 1): create_candidate no longer calls
    roundup_snapshot itself -- revision 1 of every roundup starts with
    participants [] regardless of roundup_tiles_enabled. Building the
    tiled participant list is the roundup route's job
    (post_growth_spotlight_roundup), not create_candidate's."""
    a = _make_eligible(make_person, name='DormantBranch')
    with api_tx() as tx:
        rk_w = create_candidate(tx, kind='welcome', subject_person_id=a['id'], caption='c', created_by='t')
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=a['id'])).fetchone()['u']
        tx.execute(
            """INSERT INTO spotlight_revision_consent (revision_id, person_id, role)
               SELECT current_revision_id, %(pid)s, 'subject' FROM publishing_queue
                WHERE request_key = %(rk)s LIMIT 1""",
            dict(pid=a['id'], rk=rk_w))
        set_setting(tx, 'roundup_tiles_enabled', 'true')
        try:
            rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
            rev = current_revision(tx, rk)
        finally:
            set_setting(tx, 'roundup_tiles_enabled', 'false')
    assert rev['participants'] == []
