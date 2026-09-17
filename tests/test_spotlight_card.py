import pytest
from database import api_tx
from service.spotlight.approval import make_card_token, parse_card_token, card_url, card_state
from service.spotlight.queue import create_candidate, set_setting
from service.spotlight.revisions import current_revision, attach_render
from service.spotlight.withdrawal import withdraw_member
from emails.spotlight_card_ready import card_ready_html, SUBJECT as S4
from emails.spotlight_card_live import card_live_html, share_url_for, post_url_for, SUBJECT as S5


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


def _email(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=pid)).fetchone()['email']


def test_token_roundtrip_and_expiry(monkeypatch, make_person):
    import service.spotlight.approval as ap
    p = make_person(name='TokRT')
    email = _email(p['id'])
    with api_tx() as tx:
        tok = make_card_token(tx, 'abc', email.upper())
    rk, em, nonce, status = parse_card_token(tok)
    assert rk == 'abc' and em == email.lower() and status == 'ok' and nonce
    assert parse_card_token('x.y') == (None, None, None, 'invalid')
    monkeypatch.setattr(ap, '_now_ts', lambda: 0)
    with api_tx() as tx:
        old = make_card_token(tx, 'abc', email)
    monkeypatch.undo()
    assert parse_card_token(old)[3] == 'expired'
    with api_tx() as tx:
        assert '/spotlight/card/' in card_url(tx, 'abc', email)


def test_get_is_read_only_and_post_approves(client, make_person, monkeypatch):
    import service.spotlight.storage as st
    # The object is private until scheduled (Wave 2 F09): card_state's
    # image_url is a presigned read, not the raw stored URL, so this is
    # mocked rather than pointed at a real object store.
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed/{key}')
    p = _make_eligible(make_person)
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
            attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        email = _email(p['id'])
        with api_tx() as tx:
            tok = make_card_token(tx, rk, email)
        r = client.get(f'/spotlight/card/{tok}')
        body = r.get_json()
        assert r.status_code == 200 and body['status'] == 'awaiting_member' and body['photos'][0]['uuid'] == photo
        assert body['revision'] == 1 and body['preview_available'] is True and body['image_url'] == 'https://signed/k'
        assert body['photo_uuid'] == photo and body['stale'] is False
        with api_tx('read committed') as tx:
            assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'awaiting_member'}
        r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo, 'revision': 1})
        assert r.status_code == 200 and r.get_json()['result'] == 'approved'
        with api_tx('read committed') as tx:
            assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'review'}
        # The nonce is now single-use: a repeat with the same token no
        # longer re-runs approve_card, it reports the current state.
        r2 = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo, 'revision': 1}).get_json()
        assert r2 == dict(ok=True, already=True, status='approved')
        assert client.get(f'/spotlight/card/{tok}').get_json()['status'] == 'approved'
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_post_approve_is_gated_on_settings_and_render(client, make_person, monkeypatch):
    """Route-level coverage of the two new 409s: approvals off (the owner
    decision in force for this wave), and approvals on but nothing rendered
    yet. Both return a JSON {"error": "<reason>"} body -- never a plain-text
    abort -- per Task 2 fix round 1.

    Wave 1 F11 fix round 1: the nonce is consumed only after the decision
    itself lands, so a routine 409 must NOT burn it -- the very same token
    is replayed across every step below, first against two failing
    conditions and then, once both clear, against a real approve."""
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed/{key}')
    p = _make_eligible(make_person)
    email = _email(p['id'])
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
        tok = make_card_token(tx, rk, email)
    r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo, 'revision': 1})
    assert r.status_code == 409 and r.get_json() == {'error': 'approvals_disabled'}
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        # Still the same token: approvals are on now, but nothing has been
        # rendered yet, so this 409 is also routine and must not burn it.
        r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo, 'revision': 1})
        assert r.status_code == 409 and r.get_json() == {'error': 'preview_unavailable'}
        with api_tx() as tx:
            attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        # Both conditions cleared: the same token still approves.
        r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo, 'revision': 1})
        assert r.status_code == 200 and r.get_json() == {'ok': True, 'result': 'approved'}
        # Now that the decision landed, the nonce is spent: a replay reports
        # the resolved state idempotently rather than re-running anything.
        r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo, 'revision': 1})
        assert r.status_code == 200 and r.get_json() == {'ok': True, 'already': True, 'status': 'approved'}
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_post_skip_cancels_and_wrong_email_is_403(client, make_person):
    p = _make_eligible(make_person)
    other = make_person(name='Other')
    other_email = _email(other['id'])
    email = _email(p['id'])
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        wrong_tok = make_card_token(tx, rk, other_email)
    assert client.post(f'/spotlight/card/{wrong_tok}', json={'decision': 'skip'}).status_code == 403
    with api_tx() as tx:
        tok = make_card_token(tx, rk, email)
    r = client.post(f'/spotlight/card/{tok}', json={'decision': 'skip'})
    assert r.status_code == 200
    with api_tx('read committed') as tx:
        assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'cancelled'}
    with api_tx() as tx:
        get_tok = make_card_token(tx, rk, email)
    assert client.get(f'/spotlight/card/{get_tok}').get_json()['status'] == 'skipped'


def test_bad_tokens(client, make_person):
    assert client.get('/spotlight/card/not.a.token').status_code == 400
    assert client.post('/spotlight/card/not.a.token', json={'decision': 'skip'}).status_code == 400
    p = make_person(name='NoSuchRequest')
    email = _email(p['id'])
    with api_tx() as tx:
        tok = make_card_token(tx, 'nope', email)
    assert client.get(f'/spotlight/card/{tok}').status_code == 404


def test_card_token_replay_after_withdrawal_is_rejected(make_person, client, monkeypatch):
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed/{key}')
    p = _make_eligible(make_person)
    photo = None
    with api_tx() as tx:
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
        set_setting(tx, 'approvals_enabled', 'true')
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        email = tx.execute("SELECT email FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['email']
        token = make_card_token(tx, rk, email)
    try:
        r = client.post(f'/spotlight/card/{token}', json=dict(decision='approve', photo_uuid=photo, revision=1))
        assert r.status_code == 200 and r.get_json()['ok'] is True
        r = client.post(f'/spotlight/card/{token}', json=dict(decision='approve', photo_uuid=photo, revision=1))
        assert r.status_code == 200 and r.get_json()['already'] is True
        with api_tx() as tx:
            withdraw_member(tx, p['id'], 'opt_out')
        r = client.post(f'/spotlight/card/{token}', json=dict(decision='approve', photo_uuid=photo, revision=1))
        assert r.status_code == 410 and r.get_json() == dict(error='stale')
        with api_tx() as tx:
            assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'cancelled'}
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_e4_and_e5_html_are_on_template_and_escaped():
    em_dash = chr(0x2014)  # built at runtime so the source file carries no literal em dash
    h = card_ready_html('Ri<b>vka', 'member of the week', 'https://ahavah.app/spotlight/card/t', 7, 'https://ahavah.app/u/x')
    assert 'title-card-ready.png' in h and 'Ri&lt;b&gt;vka' in h and '<b>vka' not in h and em_dash not in h and em_dash not in S4
    h = card_live_html('Sarah', 'https://cdn/x.png', 'https://www.facebook.com/1', share_url_for('https://www.facebook.com/1'), 'https://ahavah.app/u/x')
    assert 'https://cdn/x.png' in h and 'sharer.php?u=https%3A%2F%2Fwww.facebook.com%2F1' in h and em_dash not in S5
    with pytest.raises(ValueError):
        card_live_html('S', 'http://insecure/x.png', 'https://a', 'https://b', 'https://u')
    assert post_url_for('facebook', '123_456') == 'https://www.facebook.com/123_456'


def test_enqueue_card_ready_queues_once_and_is_exempt(make_person, outbox_drain):
    """E4 is queued, not sent (F07). The row is written on the CALLER'S
    transaction, `exempt=True` keeps it outside the 7-day cap (it is
    member-triggered), and the campaign_id is keyed on request_key so a
    retried caller queues nothing the second time."""
    from emails.spotlight_card_ready import enqueue_card_ready
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'card-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        first = enqueue_card_ready(tx, p['id'], rk)
        second = enqueue_card_ready(tx, p['id'], rk)      # same campaign id, idempotent
    assert first is not None and second is None
    with api_tx('read committed') as tx:
        row = tx.execute("SELECT campaign, campaign_id, exempt, state FROM email_outbox WHERE id = %(i)s",
                         dict(i=first)).fetchone()
    assert (row['campaign'], row['campaign_id'], row['exempt'], row['state']) == ('e4', f'e4-{rk}', True, 'queued')
    sent = outbox_drain(p['id'])
    assert len(sent) == 1 and '/spotlight/card/' in sent[0]['body']


def test_enqueue_card_ready_queues_nothing_for_a_deactivated_recipient(make_person, outbox_drain):
    """Wave 1 F11 fix round 1: `_Q_PERSON` filters on `activated`, so a
    member deactivated after their card candidate was created never even
    reaches the message builder -- nothing is queued, and nothing is sent.

    The email is a non-suppressed `ahavah-test.invalid` address (not the
    `make_person` fixture's default `@example.com`) so this test actually
    discriminates on the `activated` gate rather than on the outbox drain's
    unrelated suppression check, which would skip an `@example.com` row at
    send time and mask a regression here."""
    from emails.spotlight_card_ready import enqueue_card_ready
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'deactivated-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE person SET activated = FALSE WHERE id = %(id)s", dict(id=p['id']))
        assert enqueue_card_ready(tx, p['id'], rk) is None
    assert outbox_drain(p['id']) == []


def test_enqueue_card_ready_queues_nothing_when_the_token_is_none(make_person, monkeypatch, outbox_drain):
    """Decoupled from the `activated` gate above: the builder's own defensive
    check (card_url returning None) must also stop a link-less email, and it
    must do so by queuing nothing rather than by queuing a card with
    href="None"."""
    import emails.spotlight_card_ready as card_ready_mod
    monkeypatch.setattr(card_ready_mod, 'card_url', lambda tx, rk, email: None)
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'none-token-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        assert card_ready_mod.enqueue_card_ready(tx, p['id'], rk) is None
    sent = outbox_drain(p['id'])
    assert sent == []
    assert not any('href="None"' in x['body'] for x in sent)


def test_e4_is_enqueued_in_the_candidate_transaction_and_survives_restart(client, make_person,
                                                                          monkeypatch, outbox_drain):
    """The route-level guarantee F07 buys: POSTing the welcome leaves a
    DURABLE queued row, written inside the candidate's own transaction, and
    no thread anywhere. The old implementation fired a daemon thread after
    the commit, so an api restart in that window lost the invite silently."""
    import threading

    import emails.spotlight_card_ready as card_ready_mod
    assert not hasattr(card_ready_mod, 'send_card_ready_async')
    p = _make_eligible(make_person, name='E4Route')
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s",
                   dict(e=f'e4-route-{p["id"]}@ahavah-test.invalid', id=p['id']))
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        def _no_threads(*a, **kw):
            raise AssertionError('the invite must not be sent from a thread')
        monkeypatch.setattr(threading, 'Thread', _no_threads)
        r = client.post('/admin/growth/spotlight/welcome', json={'person_id': p['id']},
                        headers={'X-Growth-Cron': 'test-cron-secret'})
        monkeypatch.undo()
        assert r.status_code == 200
        rk = r.get_json()['request_key']
        with api_tx('read committed') as tx:
            row = tx.execute(
                """SELECT campaign, campaign_id, state, payload FROM email_outbox
                    WHERE person_id = %(p)s ORDER BY id DESC LIMIT 1""", dict(p=p['id'])).fetchone()
        assert (row['campaign'], row['campaign_id'], row['state']) == ('e4', f'e4-{rk}', 'queued')
        assert '/spotlight/card/' in row['payload']['html']
        assert len(outbox_drain(p['id'])) == 1
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_enqueue_card_live_wraps_share_link_and_is_idempotent(make_person, outbox_drain):
    from emails.spotlight_card_live import enqueue_card_live
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'live-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            "UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s AND platform = 'facebook'",
            dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
        assert enqueue_card_live(tx, p['id'], rk, '123_456', 'facebook') is not None
        # same campaign id, idempotent
        assert enqueue_card_live(tx, p['id'], rk, '123_456', 'facebook') is None
    sent = outbox_drain(p['id'])
    assert len(sent) == 1
    body = sent[0]['body']
    assert '/s/' in body and 'https://www.facebook.com/123_456' in body


def test_enqueue_card_live_stamps_the_share_link_with_the_row_platform(make_person, outbox_drain):
    """Fix wave I1: the share button's own campaign link carries `?p=` for the
    platform this row published on, so the click it earns splits by platform
    instead of landing under 'unknown' in `post_stats`. Instagram here, since
    the sharer dialog itself is always a www.facebook.com URL and a naive fix
    could easily have read the platform off the target instead of the row."""
    from emails.spotlight_card_live import enqueue_card_live
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s",
                   dict(e=f'live-stamp-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            "UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s AND platform = 'instagram'",
            dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
        assert enqueue_card_live(tx, p['id'], rk, '77', 'instagram') is not None
        key = tx.execute(
            """SELECT key FROM campaign_link
                WHERE kind = %(k)s AND strpos(target_url, 'sharer.php') > 0""",
            dict(k=f'post:{rk}')).fetchone()['key']
    body = outbox_drain(p['id'])[0]['body']
    assert f'/s/{key}?p=instagram' in body
    assert f'/s/{key}?p=facebook' not in body


def test_enqueue_card_live_prefers_a_supplied_https_post_url(make_person, outbox_drain):
    """Task 6: the worker's own receipt (Instagram's permalink lookup, in
    particular) may already carry the real post URL; when it is https, it
    wins over `post_url_for`'s Instagram-profile fallback."""
    from emails.spotlight_card_live import enqueue_card_live
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'live-ig-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            "UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s AND platform = 'instagram'",
            dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
        assert enqueue_card_live(tx, p['id'], rk, '77', 'instagram',
                                 post_url='https://www.instagram.com/p/abc/') is not None
    body = outbox_drain(p['id'])[0]['body']
    assert 'https://www.instagram.com/p/abc/' in body
    assert 'https://www.instagram.com/ahavah.app/' not in body


def test_enqueue_card_live_falls_back_when_post_url_is_not_https(make_person, outbox_drain):
    from emails.spotlight_card_live import enqueue_card_live, post_url_for
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'live-ig2-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            "UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s AND platform = 'instagram'",
            dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
        assert enqueue_card_live(tx, p['id'], rk, '77', 'instagram', post_url='not-a-url') is not None
    body = outbox_drain(p['id'])[0]['body']
    assert post_url_for('instagram', '77') in body


def test_choosing_another_photo_keeps_the_card_link_usable(client, make_person, monkeypatch):
    """Fix wave item 5: picking a different photo is not the member's final
    decision, so it must not burn the single-use nonce. The same link still
    GETs (not stale) and, once the new revision is rendered, approves."""
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed/{key}')
    p = _make_eligible(make_person, name='PhotoSwap')
    email = _email(p['id'])
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
               VALUES (gen_random_uuid(), %(id)s, 2, 'approved', 'x', 'y')""", dict(id=p['id']))
        second = tx.execute(
            "SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s AND position = 2",
            dict(id=p['id'])).fetchone()['u']
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
            tok = make_card_token(tx, rk, email)
        r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': second, 'revision': 1})
        assert r.status_code == 200 and r.get_json() == {'ok': True, 'result': 'new_revision', 'revision': 2}
        # The link is still live: not stale, not already resolved.
        body = client.get(f'/spotlight/card/{tok}').get_json()
        assert body['stale'] is False and body['revision'] == 2 and body['preview_available'] is False
        with api_tx() as tx:
            attach_render(tx, current_revision(tx, rk)['id'], 'h2', 'k2', 'https://cdn/k2.png')
        r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': second, 'revision': 2})
        assert r.status_code == 200 and r.get_json() == {'ok': True, 'result': 'approved'}
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_enqueue_card_live_skips_a_member_who_left_spotlight(make_person, outbox_drain):
    """The recipient query carries the consent check: a member who has opted
    out (or been deactivated) between the publish and the receipt never gets
    "your card is live" -- nothing is even queued for them."""
    from emails.spotlight_card_live import enqueue_card_live
    p = _make_eligible(make_person, name='LiveGone')
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'live-gone-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s AND platform = 'facebook'",
                   dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
        tx.execute("UPDATE person SET spotlight_opt_in = FALSE WHERE id = %(id)s", dict(id=p['id']))
        assert enqueue_card_live(tx, p['id'], rk, '123_456', 'facebook') is None
    assert outbox_drain(p['id']) == []


def test_card_state_presigns_private_preview(make_person, monkeypatch):
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: 'https://signed/' + key)
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h', f'spotlight/{rk}/1-abc-facebook.png', 'https://cdn/x.png')
        assert card_state(tx, rk)['image_url'] == f'https://signed/spotlight/{rk}/1-abc-facebook.png'


def _admin_headers(make_person):
    """A real admin bearer session, minted the way tests/test_spotlight_routes.py
    does it (copied on purpose: test files in this suite do not import from
    each other)."""
    import hashlib
    import secrets
    admin = make_person(name='CardOperator')
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        tx.execute("UPDATE person SET roles = ARRAY['admin']::TEXT[] WHERE id = %(i)s", dict(i=admin['id']))
        email = tx.execute("SELECT email FROM person WHERE id = %(i)s", dict(i=admin['id'])).fetchone()['email']
        tx.execute(
            """INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
               VALUES (%(h)s, %(e)s, %(p)s, TRUE, '123456')""",
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=admin['id']))
    return {'Authorization': f'Bearer {tok}'}


def _consent_rows(rk):
    with api_tx('read committed') as tx:
        return [(r['revision'], r['person_id'], r['role']) for r in tx.execute(
            """SELECT r.revision, c.person_id, c.role
                 FROM spotlight_revision_consent c
                 JOIN spotlight_revision r ON r.id = c.revision_id
                WHERE r.request_key = %(rk)s
                ORDER BY r.revision""", dict(rk=rk)).fetchall()]


def _revision_count(rk):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT count(*) AS n FROM spotlight_revision WHERE request_key = %(rk)s",
                          dict(rk=rk)).fetchone()['n']


def _nonce_used(nonce):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT used_at FROM spotlight_token_nonce WHERE nonce = %(n)s",
                          dict(n=nonce)).fetchone()['used_at'] is not None


def test_stale_tab_approval_records_nothing_and_keeps_the_link(client, make_person, monkeypatch):
    """Wave 3d Task 2, the acceptance probe's member C: the member opens the
    card (revision 1 on screen), an operator rewrites the caption and the
    tick renders revision 2, then the member presses Approve on the tab that
    still shows revision 1. Consent must never land on a revision the page
    did not show: the POST names revision 1, the answer is new_revision with
    the current number, nothing is recorded on either revision, no extra
    revision is made, and the link stays usable for the card that is now
    current."""
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed/{key}')
    p = _make_eligible(make_person, name='StaleTab')
    A = _admin_headers(make_person)
    email = _email(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            attach_render(tx, current_revision(tx, rk)['id'], 'h1', 'k1', 'https://cdn/k1.png')
            tok = make_card_token(tx, rk, email)
            qid = tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s ORDER BY id LIMIT 1",
                             dict(rk=rk)).fetchone()['id']
        nonce = parse_card_token(tok)[2]

        shown = client.get(f'/spotlight/card/{tok}').get_json()
        assert shown['revision'] == 1 and shown['preview_available'] is True

        r = client.post(f'/admin/growth/queue/{qid}/caption',
                        json={'caption': 'Operator rewrote this caption'}, headers=A)
        assert r.status_code == 200
        with api_tx() as tx:
            rev2 = current_revision(tx, rk)
            assert rev2['revision'] == 2
            attach_render(tx, rev2['id'], 'h2', 'k2', 'https://cdn/k2.png')

        r = client.post(f'/spotlight/card/{tok}', json={
            'decision': 'approve', 'photo_uuid': shown['photo_uuid'], 'revision': shown['revision']})
        assert r.status_code == 200
        assert r.get_json() == {'ok': True, 'result': 'new_revision', 'revision': 2}
        assert _consent_rows(rk) == []
        assert _revision_count(rk) == 2
        assert _nonce_used(nonce) is False

        again = client.get(f'/spotlight/card/{tok}').get_json()
        assert again['stale'] is False and again['revision'] == 2 and again['status'] == 'awaiting_member'

        r = client.post(f'/spotlight/card/{tok}', json={
            'decision': 'approve', 'photo_uuid': again['photo_uuid'], 'revision': again['revision']})
        assert r.status_code == 200 and r.get_json() == {'ok': True, 'result': 'approved'}
        assert _consent_rows(rk) == [(2, p['id'], 'subject')]
        assert _nonce_used(nonce) is True
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


@pytest.mark.parametrize('revision', ['missing', None, '1', True, 1.0])
def test_approve_without_an_integer_revision_is_400(client, make_person, monkeypatch, revision):
    """The approve body must name the revision the page showed, as a real
    integer: missing, null, a numeric string, a bool (a Python int subclass)
    and a float all answer 400, record nothing and leave the link usable."""
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed/{key}')
    p = _make_eligible(make_person, name='NoRevision')
    email = _email(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s",
                               dict(id=p['id'])).fetchone()['u']
            attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
            tok = make_card_token(tx, rk, email)
        body = {'decision': 'approve', 'photo_uuid': photo}
        if revision != 'missing':
            body['revision'] = revision
        r = client.post(f'/spotlight/card/{tok}', json=body)
        assert r.status_code == 400
        assert _consent_rows(rk) == []
        assert _nonce_used(parse_card_token(tok)[2]) is False
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


@pytest.mark.parametrize('revision', ['missing', None])
def test_paused_approvals_answer_before_the_revision_is_read(client, make_person, monkeypatch, revision):
    """Fix wave B (M4): while approvals are off, a page served before Wave 3d
    (its approve body names no revision) must still read the paused answer
    (409 approvals_disabled), not a 400 the page shows as an error. Nothing
    is recorded and the link stays usable for when approvals open."""
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed/{key}')
    p = _make_eligible(make_person, name='PausedNoRevision')
    email = _email(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'false')
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s",
                           dict(id=p['id'])).fetchone()['u']
        attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        tok = make_card_token(tx, rk, email)
    body = {'decision': 'approve', 'photo_uuid': photo}
    if revision != 'missing':
        body['revision'] = revision
    r = client.post(f'/spotlight/card/{tok}', json=body)
    assert r.status_code == 409 and r.get_json() == {'error': 'approvals_disabled'}
    assert _consent_rows(rk) == []
    assert _nonce_used(parse_card_token(tok)[2]) is False
