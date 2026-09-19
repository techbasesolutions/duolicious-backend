import secrets
import pytest, uuid
from database import api_tx
from service.campaigns import make_campaign_link
from service.config import WEB_BASE_URL
from service.spotlight.approval import make_card_token
from service.spotlight.queue import create_candidate, set_setting
from service.spotlight.revisions import (create_revision, current_revision, attach_render, record_consent,
                                         consent_complete, edit_caption, approve_card)


def _make_eligible(make_person, name='Elig', gender='Woman'):
    p = make_person(name=name, gender=gender)
    with api_tx() as tx:
        tx.execute("""
            UPDATE person SET spotlight_opt_in = TRUE, spotlight_opt_in_at = NOW(),
                   ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01',
                   deletion_requested_at = NULL, spotlight_last_featured_at = NULL
             WHERE id = %(id)s""", dict(id=p['id']))
        # photo has NOT NULL blurhash and hash columns with no default (checked \d photo);
        # uuid is a text column, and a real photo id is a 64-character hex string
        # (upload_photo mints them with secrets.token_hex(32)), so the fixture does too.
        tx.execute("""
            INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
            VALUES (%(u)s, %(id)s, 1, 'approved', 'testblurhash', gen_random_uuid()::text)""", dict(u=secrets.token_hex(32), id=p['id']))
    return p


def _photo(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s ORDER BY position LIMIT 1", dict(id=pid)).fetchone()['u']


def _statuses(rk):
    with api_tx('read committed') as tx:
        return {r['status'] for r in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()}


def test_candidate_creates_revision_one(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='Welcome', created_by='t')
        rev = current_revision(tx, rk)
        rows = tx.execute("SELECT current_revision_id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()
    assert rev['revision'] == 1 and rev['asset_hash'] is None and rev['photo_uuid'] is not None
    assert all(r['current_revision_id'] == rev['id'] for r in rows)


def test_caption_edit_creates_new_revision_and_drops_consent(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='Welcome', created_by='t')
            rev1 = current_revision(tx, rk)
            attach_render(tx, rev1['id'], 'hash1', 'k1', 'https://cdn/k1.png')
            assert approve_card(tx, rk, p['id'], photo, shown_revision=1) == 'approved'
            assert consent_complete(tx, rev1['id']) is True
            rev2_id = edit_caption(tx, rk, 'Welcome, changed', 't')
            assert rev2_id != rev1['id']
            assert consent_complete(tx, rev2_id) is False
            assert consent_complete(tx, rev1['id']) is True          # old consent untouched but no longer current
            assert current_revision(tx, rk)['id'] == rev2_id
            with pytest.raises(ValueError, match='preview_unavailable'):
                approve_card(tx, rk, p['id'], photo, shown_revision=2)  # revision 2 not rendered yet
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_edit_caption_keeps_the_original_link_not_a_later_e5_share_link(make_person):
    """The lookup used to be `LIMIT 1` with no ordering, which is only ever
    safe by accident of physical row layout. A second `campaign_link` of the
    same `post:<rk>` kind -- exactly what E5's 'card live' email mints for
    its own share button -- must never win just because it happens to be
    scanned first. Ordering by `created_at ASC` makes the caption link,
    always minted first at candidate creation, win regardless of physical
    row order.

    Reproduced deterministically: the original row is deleted and
    reinserted (giving it a LATER physical position than the E5 link) with
    its `created_at` backdated to before the E5 link's. An unordered
    `LIMIT 1` scans physical order and would return the E5 link here; the
    `created_at ASC` fix must still return the original."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='Welcome', created_by='t')
        kind = f'post:{rk}'
        original = tx.execute(
            "SELECT key, target_url, subject_person_id FROM campaign_link WHERE kind = %(k)s",
            dict(k=kind)).fetchone()
        # Stand in for E5's second, later mint of the same kind (its share
        # button gets its own link to the live post).
        make_campaign_link(tx, kind, f'{WEB_BASE_URL}/discover', None)
        # Force the original row to a LATER physical position than the E5
        # link, so an unordered scan returns the E5 link first, then
        # backdate its created_at so the correct, time-ordered answer is
        # still the original.
        tx.execute("DELETE FROM campaign_link WHERE key = %(k)s", dict(k=original['key']))
        tx.execute(
            """INSERT INTO campaign_link (key, kind, target_url, subject_person_id, created_at)
               VALUES (%(k)s, %(kind)s, %(url)s, %(pid)s, NOW() - interval '1 hour')""",
            dict(k=original['key'], kind=kind, url=original['target_url'], pid=original['subject_person_id']))
        edit_caption(tx, rk, 'Welcome, updated', 't')
        rows = tx.execute("SELECT caption FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()
    assert rows
    for row in rows:
        assert f'/s/{original["key"]}' in row['caption']


def test_edit_of_in_flight_row_is_refused(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='New this week', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'scheduled' WHERE request_key = %(rk)s", dict(rk=rk))
        with pytest.raises(ValueError, match='in_flight'):
            edit_caption(tx, rk, 'x', 't')


def test_approvals_disabled_by_default(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        with pytest.raises(ValueError, match='approvals_disabled'):
            approve_card(tx, rk, p['id'], photo, shown_revision=1)


def test_different_photo_makes_new_revision_without_consent(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash) VALUES (%(u)s, %(id)s, 2, 'approved', 'x', 'y')", dict(u=secrets.token_hex(32), id=p['id']))
        second = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s AND position = 2", dict(id=p['id'])).fetchone()['u']
    # adapt the INSERT to the real NOT NULL photo columns recorded in the Phase B task-2 report
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            rev1 = current_revision(tx, rk)
            attach_render(tx, rev1['id'], 'h', 'k', 'https://cdn/k.png')
            assert approve_card(tx, rk, p['id'], second, shown_revision=1) == 'new_revision'
            rev2 = current_revision(tx, rk)
            assert rev2['id'] != rev1['id'] and rev2['photo_uuid'] == second and rev2['asset_hash'] is None
            assert consent_complete(tx, rev2['id']) is False
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_welcome_consent_does_not_satisfy_roundup(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk_w = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            attach_render(tx, current_revision(tx, rk_w)['id'], 'h', 'k', 'https://cdn/k.png')
            assert approve_card(tx, rk_w, p['id'], photo, shown_revision=1) == 'approved'
            rk_r = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
            rid = create_revision(tx, rk_r, caption='r', photo_uuid=None,
                                  participants=[dict(person_id=p['id'], first_name='Elig',
                                                     photo_url=f'https://img/450-{photo}.jpg',
                                                     photo_uuid=photo)],
                                  channels=['facebook','instagram'], created_by='t')
            assert consent_complete(tx, rid) is False
            assert record_consent(tx, rid, p['id'], 'participant') is True
            assert consent_complete(tx, rid) is True
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_count_only_roundup_is_consent_complete():
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        assert consent_complete(tx, current_revision(tx, rk)['id']) is True


def test_attach_render_is_one_shot():
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        rid = current_revision(tx, rk)['id']
        attach_render(tx, rid, 'h', 'k', 'https://cdn/k.png')
        with pytest.raises(ValueError, match='already_rendered'):
            attach_render(tx, rid, 'h2', 'k2', 'https://cdn/k2.png')


def test_attach_render_raises_not_found_for_a_missing_revision():
    with api_tx() as tx:
        with pytest.raises(ValueError, match='not_found'):
            attach_render(tx, 999999999, 'h', 'k', 'https://cdn/k.png')


def test_create_revision_refuses_a_terminal_request(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'published' WHERE request_key = %(rk)s", dict(rk=rk))
        with pytest.raises(ValueError, match='terminal'):
            edit_caption(tx, rk, 'x', 't')
        tx.execute("UPDATE publishing_queue SET status = 'cancelled' WHERE request_key = %(rk)s", dict(rk=rk))
        with pytest.raises(ValueError, match='terminal'):
            edit_caption(tx, rk, 'x', 't')


def test_approve_from_an_older_revision_records_nothing(make_person):
    """Wave 3d Task 2: approve_card is told which revision the member was
    shown. When that is not the current revision it records no consent on
    either revision, makes no new revision, and answers new_revision so the
    page re-reads and asks about the current card. The current number then
    approves as normal."""
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='Welcome', created_by='t')
            rev1 = current_revision(tx, rk)
            attach_render(tx, rev1['id'], 'hash1', 'k1', 'https://cdn/k1.png')
            rev2_id = edit_caption(tx, rk, 'Welcome, changed', 't')
            attach_render(tx, rev2_id, 'hash2', 'k2', 'https://cdn/k2.png')
            assert approve_card(tx, rk, p['id'], photo, shown_revision=1) == 'new_revision'
            assert consent_complete(tx, rev1['id']) is False
            assert consent_complete(tx, rev2_id) is False
            assert current_revision(tx, rk)['id'] == rev2_id
            assert tx.execute("SELECT count(*) AS n FROM spotlight_revision WHERE request_key = %(rk)s",
                              dict(rk=rk)).fetchone()['n'] == 2
            assert approve_card(tx, rk, p['id'], photo, shown_revision=2) == 'approved'
            assert consent_complete(tx, rev2_id) is True
            assert consent_complete(tx, rev1['id']) is False
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_production_shaped_photo_id_carries_through_the_whole_welcome_path(client, make_person, monkeypatch):
    """A real photo id is a 64-character hex string, not an RFC uuid.

    `photo.uuid` and `onboardee_photo.uuid` are `text`, and
    `service.person.upload_photo` mints their ids with
    `secrets.token_hex(32)`. Every fixture in this suite used
    `gen_random_uuid()` instead, so the whole spotlight path was only ever
    exercised with RFC-shaped ids and a production id was never tried.

    `spotlight_revision.photo_uuid` was declared `uuid` (migration 0044) and
    `create_revision` inserted through `%(photo)s::uuid`, so in production
    every welcome and member-of-week card for a real member raised
    `psycopg.errors.InvalidTextRepresentation: invalid input syntax for type
    uuid` and the welcome route answered 500.

    The whole path is walked with a production-shaped id: the route creates
    the candidate, the revision stores the id byte for byte, the member's
    card GET hands it back, and approving with it records consent."""
    import service.api.admin.spotlight_routes as sr
    import service.spotlight.storage as st
    monkeypatch.setattr(sr, '_enqueue_card_ready', lambda tx, pid, rk: None)
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed/{key}')
    photo_id = secrets.token_hex(32)
    assert len(photo_id) == 64
    p = _make_eligible(make_person, name='HexPhotoId')
    with api_tx() as tx:
        tx.execute("UPDATE photo SET uuid = %(u)s WHERE person_id = %(id)s", dict(u=photo_id, id=p['id']))
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        r = client.post('/admin/growth/spotlight/welcome', json={'person_id': p['id']},
                        headers={'X-Growth-Cron': 'test-cron-secret'})
        assert r.status_code == 200, r.get_data(as_text=True)
        rk = r.get_json()['request_key']
        with api_tx() as tx:
            rev = current_revision(tx, rk)
            assert rev['photo_uuid'] == photo_id
            attach_render(tx, rev['id'], 'hash-hex', 'key-hex', 'https://cdn/key-hex.png')
            token = make_card_token(tx, rk, email)
        card = client.get(f'/spotlight/card/{token}')
        assert card.status_code == 200
        body = card.get_json()
        assert body['photo_uuid'] == photo_id
        assert [ph['uuid'] for ph in body['photos']] == [photo_id]
        decision = client.post(f'/spotlight/card/{token}',
                               json={'decision': 'approve', 'photo_uuid': photo_id,
                                     'revision': rev['revision']})
        assert decision.status_code == 200, decision.get_data(as_text=True)
        assert decision.get_json()['result'] == 'approved'
        with api_tx('read committed') as tx:
            assert consent_complete(tx, rev['id']) is True
            stored = tx.execute(
                "SELECT photo_uuid::text AS u FROM spotlight_revision WHERE id = %(id)s",
                dict(id=rev['id'])).fetchone()['u']
        assert stored == photo_id
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')
