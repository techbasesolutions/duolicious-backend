"""service.spotlight.assets: content-hashed keys and their compare-and-set
attach/completion (Wave 2 F06 part 2; fix round 1 rulings)."""
import hashlib
import secrets

from database import api_tx
from service.spotlight.assets import asset_key, attach_platform_image, complete_render_if_ready
from service.spotlight.queue import create_candidate
from service.spotlight.revisions import create_revision, current_revision


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_asset_key_extension_follows_the_content_type():
    """Wave 3d Task 6: a JPEG card is stored under a `.jpg` key and a PNG
    card (an admin deployed before the change) under `.png`, so the key
    names what the object really is."""
    sha = _sha(b'bytes')
    assert asset_key('rk', 7, sha, 'instagram', 'image/jpeg') == f'spotlight/rk/7-{sha[:16]}-instagram.jpg'
    assert asset_key('rk', 7, sha, 'facebook', 'image/png') == f'spotlight/rk/7-{sha[:16]}-facebook.png'


def test_complete_render_if_ready_is_false_until_every_row_has_a_hash():
    """Sanity: a set with only one platform's image_sha256 stamped is not
    ready, and no render is attached."""
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        rev_id = current_revision(tx, rk)['id']
        sha = _sha(b'bytes')
        key = asset_key(rk, rev_id, sha, 'facebook')
        assert attach_platform_image(tx, rk, 'facebook', rev_id, key, f'https://cdn/{key}', sha) == ('attached', None)
        assert complete_render_if_ready(tx, rk, rev_id) is False
        assert current_revision(tx, rk)['asset_hash'] is None


def test_attach_platform_image_hands_back_the_key_it_displaced():
    """Fix wave item 6: the contract the route's orphan cleanup depends on.
    The first attach displaces nothing (None); the second, for the same
    un-rendered revision with different bytes, hands back the first key so
    the caller can queue the object it just stopped naming. Read inside the
    UPDATE's own statement, so it is the value the statement actually
    replaced rather than one read separately beforehand."""
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        rev_id = current_revision(tx, rk)['id']
        sha1, sha2 = _sha(b'displaced-one'), _sha(b'displaced-two')
        key1 = asset_key(rk, rev_id, sha1, 'facebook')
        key2 = asset_key(rk, rev_id, sha2, 'facebook')
        assert attach_platform_image(tx, rk, 'facebook', rev_id, key1, f'https://cdn/{key1}', sha1) == \
            ('attached', None)
        assert attach_platform_image(tx, rk, 'facebook', rev_id, key2, f'https://cdn/{key2}', sha2) == \
            ('attached', key1)


def test_attach_platform_image_refuses_once_the_revision_is_rendered():
    """Fix round 1 (ruling 4): the compare-and-set also refuses once the
    pinned revision already has an attached render (its asset_hash is set),
    even though the revision/status check alone would still pass -- this is
    what makes a second, concurrent upload for the SAME platform and
    revision lose (409 superseded, at the route) rather than silently
    overwrite the row backing an already-rendered revision with different
    bytes than the ones the revision's own image columns point at."""
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        rev_id = current_revision(tx, rk)['id']
        first_sha = _sha(b'first-bytes')
        fb_key = asset_key(rk, rev_id, first_sha, 'facebook')
        ig_key = asset_key(rk, rev_id, first_sha, 'instagram')
        assert attach_platform_image(tx, rk, 'facebook', rev_id, fb_key, f'https://cdn/{fb_key}', first_sha)[0] == 'attached'
        assert attach_platform_image(tx, rk, 'instagram', rev_id, ig_key, f'https://cdn/{ig_key}', first_sha)[0] == 'attached'
        assert complete_render_if_ready(tx, rk, rev_id) is True

        second_sha = _sha(b'second-bytes')
        fb_key2 = asset_key(rk, rev_id, second_sha, 'facebook')
        outcome = attach_platform_image(tx, rk, 'facebook', rev_id, fb_key2, f'https://cdn/{fb_key2}', second_sha)
        # Fix wave item 6: a superseded attach changed no row, so it displaced
        # no key either and there is nothing for the route to clean up beyond
        # the object it just uploaded.
        assert outcome == ('superseded', None)

        row = tx.execute(
            "SELECT image_key, image_sha256 FROM publishing_queue WHERE request_key = %(rk)s AND platform = 'facebook'",
            dict(rk=rk)).fetchone()
        assert row['image_key'] == fb_key and row['image_sha256'] == first_sha


def test_complete_render_refuses_while_a_sibling_is_parked_with_unresolved_delivery():
    """Task 5 residual (re-review). `create_revision`'s current_revision_id
    repoint is unconditional over every non-terminal row, but its un-render
    step deliberately skips a row parked in `review` with `delivery_state`
    `attempting`/`delivery_unknown` (it may have a live post behind it) --
    so after a re-issue that row can point at a BRAND NEW revision while
    still carrying the OLD revision's image_key/sha256. Without the fix,
    `complete_render_if_ready` would treat that stale pair as a match and
    pin the new revision's render to facebook's stale key the moment the
    still-active platform (instagram) uploads its own fresh render."""
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        rev1 = current_revision(tx, rk)['id']
        # One render uploaded to both platforms (fix wave B, M2: a set whose
        # platform rows carry different bytes never completes).
        fb_sha1 = ig_sha1 = _sha(b'rev1')
        fb_key1 = asset_key(rk, rev1, fb_sha1, 'facebook')
        ig_key1 = asset_key(rk, rev1, ig_sha1, 'instagram')
        assert attach_platform_image(tx, rk, 'facebook', rev1, fb_key1, f'https://cdn/{fb_key1}', fb_sha1)[0] == 'attached'
        assert attach_platform_image(tx, rk, 'instagram', rev1, ig_key1, f'https://cdn/{ig_key1}', ig_sha1)[0] == 'attached'
        assert complete_render_if_ready(tx, rk, rev1) is True

        # Facebook's post went out but its receipt could not confirm the
        # result -- parked in review with delivery_state 'delivery_unknown',
        # exactly as record_receipt leaves it, still carrying rev1's artwork.
        tx.execute(
            """UPDATE publishing_queue SET status = 'review', delivery_state = 'delivery_unknown'
                WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(rk=rk))

        # A caption edit re-issues the request. The default create_revision
        # path repoints every non-terminal row -- facebook included -- onto
        # the new revision, but its un-render scope leaves facebook's stale
        # rev1 image columns untouched.
        rev = current_revision(tx, rk)
        rev2 = create_revision(tx, rk, caption='c2', photo_uuid=None, participants=[],
                               channels=rev['channels'], layout_version=rev['layout_version'], created_by='t')
        fb_row = tx.execute(
            """SELECT current_revision_id, image_key, image_sha256 FROM publishing_queue
                WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(rk=rk)).fetchone()
        assert fb_row['current_revision_id'] == rev2 and fb_row['image_key'] == fb_key1

        ig_sha2 = _sha(b'ig-rev2')
        ig_key2 = asset_key(rk, rev2, ig_sha2, 'instagram')
        assert attach_platform_image(tx, rk, 'instagram', rev2, ig_key2, f'https://cdn/{ig_key2}', ig_sha2)[0] == 'attached'
        assert complete_render_if_ready(tx, rk, rev2) is False
        assert current_revision(tx, rk)['asset_hash'] is None

        # And a direct attempt to re-render facebook itself for rev2 is
        # refused outright by attach_platform_image's own guard.
        fb_key2 = asset_key(rk, rev2, _sha(b'fb-rev2'), 'facebook')
        assert attach_platform_image(tx, rk, 'facebook', rev2, fb_key2, f'https://cdn/{fb_key2}',
                                     _sha(b'fb-rev2')) == ('superseded', None)


_CRON = {'X-Growth-Cron': 'test-cron-secret'}


def _two_platform_roundup_backdated(tx):
    """A roundup placed ahead of every row already in the shared table, so
    the tick's 200-row render listing reaches it whatever earlier tests
    left behind."""
    rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
    tx.execute(
        """UPDATE publishing_queue
              SET created_at = (SELECT COALESCE(MIN(created_at), NOW()) - interval '1 day' FROM publishing_queue)
            WHERE request_key = %(rk)s""", dict(rk=rk))
    return rk


def test_platform_renders_with_different_bytes_do_not_complete_and_are_listed_again(client):
    """Fix wave B (M2): across two ticks each platform row can attach a
    different render of the same revision. Completing on facebook's hash
    alone would let instagram publish bytes the member never saw. So the set
    does not complete, the revision stays unrendered, both rows lose their
    artwork (the next tick re-uploads one render to both) and their keys are
    queued for deletion, and the card is listed for rendering again."""
    with api_tx() as tx:
        rk = _two_platform_roundup_backdated(tx)
        rev_id = current_revision(tx, rk)['id']
        fb_sha, ig_sha = _sha(secrets.token_bytes(16)), _sha(secrets.token_bytes(16))
        fb_key = asset_key(rk, rev_id, fb_sha, 'facebook', 'image/jpeg')
        ig_key = asset_key(rk, rev_id, ig_sha, 'instagram', 'image/jpeg')
        assert attach_platform_image(tx, rk, 'facebook', rev_id, fb_key, f'https://cdn/{fb_key}', fb_sha)[0] == 'attached'
        assert complete_render_if_ready(tx, rk, rev_id) is False
    with api_tx() as tx:
        assert attach_platform_image(tx, rk, 'instagram', rev_id, ig_key, f'https://cdn/{ig_key}', ig_sha)[0] == 'attached'
        assert complete_render_if_ready(tx, rk, rev_id) is False
    try:
        with api_tx('read committed') as tx:
            rev = current_revision(tx, rk)
            assert (rev['asset_hash'], rev['image_key']) == (None, None)
            rows = tx.execute(
                """SELECT image_key, image_url, image_sha256 FROM publishing_queue
                    WHERE request_key = %(rk)s""", dict(rk=rk)).fetchall()
            assert len(rows) == 2
            assert all((r['image_key'], r['image_url'], r['image_sha256']) == (None, None, None) for r in rows)
            queued = {r['target'] for r in tx.execute(
                "SELECT target FROM cleanup_job WHERE target = ANY(%(k)s::text[]) AND state = 'pending'",
                dict(k=[fb_key, ig_key])).fetchall()}
            assert queued == {fb_key, ig_key}
        r = client.get('/admin/growth/queue?needs_render=1', headers=_CRON)
        assert r.status_code == 200
        assert {row['platform'] for row in r.get_json() if row['request_key'] == rk} == {'facebook', 'instagram'}
    finally:
        with api_tx() as tx:
            tx.execute("UPDATE publishing_queue SET status = 'cancelled' WHERE request_key = %(rk)s", dict(rk=rk))


def test_platform_renders_with_identical_bytes_complete_as_before(client):
    """Fix wave B (M2), the ordinary case: one render uploaded to both
    platforms completes the set, pinned to facebook's row, and the card is
    no longer listed for rendering."""
    with api_tx() as tx:
        rk = _two_platform_roundup_backdated(tx)
        rev_id = current_revision(tx, rk)['id']
        sha = _sha(secrets.token_bytes(16))
        fb_key = asset_key(rk, rev_id, sha, 'facebook', 'image/jpeg')
        ig_key = asset_key(rk, rev_id, sha, 'instagram', 'image/jpeg')
        assert attach_platform_image(tx, rk, 'instagram', rev_id, ig_key, f'https://cdn/{ig_key}', sha)[0] == 'attached'
        assert complete_render_if_ready(tx, rk, rev_id) is False
        assert attach_platform_image(tx, rk, 'facebook', rev_id, fb_key, f'https://cdn/{fb_key}', sha)[0] == 'attached'
        assert complete_render_if_ready(tx, rk, rev_id) is True
    try:
        with api_tx('read committed') as tx:
            rev = current_revision(tx, rk)
            assert (rev['asset_hash'], rev['image_key']) == (sha, fb_key)
            assert {r['image_sha256'] for r in tx.execute(
                "SELECT image_sha256 FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {sha}
        r = client.get('/admin/growth/queue?needs_render=1', headers=_CRON)
        assert r.status_code == 200
        assert not [row for row in r.get_json() if row['request_key'] == rk]
    finally:
        with api_tx() as tx:
            tx.execute("UPDATE publishing_queue SET status = 'cancelled' WHERE request_key = %(rk)s", dict(rk=rk))
