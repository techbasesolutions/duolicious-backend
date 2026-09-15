"""service.spotlight.assets: content-hashed keys and their compare-and-set
attach/completion (Wave 2 F06 part 2; fix round 1 rulings)."""
import hashlib

from database import api_tx
from service.spotlight.assets import asset_key, attach_platform_image, complete_render_if_ready
from service.spotlight.queue import create_candidate
from service.spotlight.revisions import create_revision, current_revision


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_complete_render_if_ready_is_false_until_every_row_has_a_hash():
    """Sanity: a set with only one platform's image_sha256 stamped is not
    ready, and no render is attached."""
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        rev_id = current_revision(tx, rk)['id']
        sha = _sha(b'bytes')
        key = asset_key(rk, rev_id, sha, 'facebook')
        assert attach_platform_image(tx, rk, 'facebook', rev_id, key, f'https://cdn/{key}', sha) == 'attached'
        assert complete_render_if_ready(tx, rk, rev_id) is False
        assert current_revision(tx, rk)['asset_hash'] is None


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
        assert attach_platform_image(tx, rk, 'facebook', rev_id, fb_key, f'https://cdn/{fb_key}', first_sha) == 'attached'
        assert attach_platform_image(tx, rk, 'instagram', rev_id, ig_key, f'https://cdn/{ig_key}', first_sha) == 'attached'
        assert complete_render_if_ready(tx, rk, rev_id) is True

        second_sha = _sha(b'second-bytes')
        fb_key2 = asset_key(rk, rev_id, second_sha, 'facebook')
        outcome = attach_platform_image(tx, rk, 'facebook', rev_id, fb_key2, f'https://cdn/{fb_key2}', second_sha)
        assert outcome == 'superseded'

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
        fb_sha1 = _sha(b'fb-rev1'); ig_sha1 = _sha(b'ig-rev1')
        fb_key1 = asset_key(rk, rev1, fb_sha1, 'facebook')
        ig_key1 = asset_key(rk, rev1, ig_sha1, 'instagram')
        assert attach_platform_image(tx, rk, 'facebook', rev1, fb_key1, f'https://cdn/{fb_key1}', fb_sha1) == 'attached'
        assert attach_platform_image(tx, rk, 'instagram', rev1, ig_key1, f'https://cdn/{ig_key1}', ig_sha1) == 'attached'
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
        assert attach_platform_image(tx, rk, 'instagram', rev2, ig_key2, f'https://cdn/{ig_key2}', ig_sha2) == 'attached'
        assert complete_render_if_ready(tx, rk, rev2) is False
        assert current_revision(tx, rk)['asset_hash'] is None

        # And a direct attempt to re-render facebook itself for rev2 is
        # refused outright by attach_platform_image's own guard.
        fb_key2 = asset_key(rk, rev2, _sha(b'fb-rev2'), 'facebook')
        assert attach_platform_image(tx, rk, 'facebook', rev2, fb_key2, f'https://cdn/{fb_key2}',
                                     _sha(b'fb-rev2')) == 'superseded'
