"""service.spotlight.assets: content-hashed keys and their compare-and-set
attach/completion (Wave 2 F06 part 2; fix round 1 rulings)."""
import hashlib

from database import api_tx
from service.spotlight.assets import asset_key, attach_platform_image, complete_render_if_ready
from service.spotlight.queue import create_candidate
from service.spotlight.revisions import current_revision


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
