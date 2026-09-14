"""Spotlight consent (spec 3.1): API write path + signed confirm endpoints.

`person.spotlight_opt_in` / `spotlight_opt_in_at` (migration 0039) are set
via `PATCH /profile-info` (authenticated) or via the unauthenticated
`/spotlight/confirm/<token>` link sent in the invite email (E1). The GET
confirm endpoint must never mutate state; only the POST opts a member in.
"""
from database import api_tx
from service.spotlight import set_spotlight_opt_in, spotlight_confirm_url, make_confirm_token
from service.spotlight.withdrawal import withdraw_member


def _flag(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT spotlight_opt_in, spotlight_opt_in_at FROM person WHERE id = %(id)s", dict(id=pid)).fetchone()


def test_set_opt_in_stamps_time(make_person):
    p = make_person(name='Opt')
    with api_tx() as tx:
        set_spotlight_opt_in(tx, p['id'], True)
    row = _flag(p['id'])
    assert row['spotlight_opt_in'] is True and row['spotlight_opt_in_at'] is not None
    with api_tx() as tx:
        set_spotlight_opt_in(tx, p['id'], False)
    assert _flag(p['id'])['spotlight_opt_in'] is False


def test_get_confirm_changes_nothing_and_post_opts_in(client, make_person):
    p = make_person(name='Link')
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
        token = make_confirm_token(tx, email)
    r = client.get(f'/spotlight/confirm/{token}')
    assert r.status_code == 200 and r.get_json()['already'] is False
    assert _flag(p['id'])['spotlight_opt_in'] is False          # GET never mutates
    r = client.post(f'/spotlight/confirm/{token}')
    assert r.status_code == 200 and _flag(p['id'])['spotlight_opt_in'] is True
    assert client.post(f'/spotlight/confirm/{token}').get_json() == dict(ok=True, already=True)  # idempotent
    assert client.post('/spotlight/confirm/not.a.token').status_code == 400


def test_expired_token_is_rejected(client, make_person, monkeypatch):
    import service.spotlight as sp
    p = make_person(name='Old')
    monkeypatch.setattr(sp, '_now_ts', lambda: 0)          # token minted at epoch
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
        token = make_confirm_token(tx, email)
    monkeypatch.undo()
    assert client.post(f'/spotlight/confirm/{token}').status_code == 410


def test_confirm_url_shape(make_person):
    p = make_person(name='UrlShape')
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
        url = spotlight_confirm_url(tx, email)
    assert url.startswith('https://') and '/spotlight/confirm/' in url


def test_confirm_replay_after_withdrawal_is_rejected(make_person, client):
    p = make_person(name='Replay')
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['email']
        token = make_confirm_token(tx, email)
    assert client.post(f'/spotlight/confirm/{token}').get_json() == dict(ok=True, already=False)
    assert client.post(f'/spotlight/confirm/{token}').get_json() == dict(ok=True, already=True)   # ordinary repeat
    with api_tx() as tx:
        set_spotlight_opt_in(tx, p['id'], False)   # withdrawal bumps the epoch
    r = client.post(f'/spotlight/confirm/{token}')
    assert r.status_code == 410 and r.get_json() == dict(error='stale')
    with api_tx() as tx:
        assert tx.execute("SELECT spotlight_opt_in AS v FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['v'] is False
        fresh = make_confirm_token(tx, email)
    assert client.post(f'/spotlight/confirm/{fresh}').get_json() == dict(ok=True, already=False)
    with api_tx() as tx:
        assert tx.execute("SELECT spotlight_opt_in AS v FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['v'] is True


def test_confirm_get_is_read_only_and_reports_stale(make_person, client):
    p = make_person(name='Getter')
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['email']
        token = make_confirm_token(tx, email)
    assert client.get(f'/spotlight/confirm/{token}').get_json()['stale'] is False
    with api_tx() as tx:
        assert tx.execute("SELECT used_at FROM spotlight_token_nonce WHERE person_id = %(p)s", dict(p=p['id'])).fetchone()['used_at'] is None
        withdraw_member(tx, p['id'], 'opt_out')
    assert client.get(f'/spotlight/confirm/{token}').get_json()['stale'] is True


def test_old_format_token_is_invalid(client):
    assert client.post('/spotlight/confirm/abc.123.sig').status_code == 400


def test_spotlight_opt_in_surfaced_in_profile_info(make_person):
    from service.person.sql import Q_GET_PROFILE_INFO

    p = make_person(name='Bright')
    with api_tx() as tx:
        set_spotlight_opt_in(tx, p['id'], True)
    with api_tx('read committed') as tx:
        info = tx.execute(
            Q_GET_PROFILE_INFO,
            dict(person_id=p['id'], email='x@example.com'),
        ).fetchone()['j']
    assert info['spotlight_opt_in'] is True


def test_explicit_null_spotlight_opt_in_is_a_no_op(make_person):
    """M-d ruling: an explicit `{"spotlight_opt_in": null}` is a no-op, never
    an opt-out.

    Two layers hold that. At the edge, PatchProfileInfo's check_exactly_one
    validator refuses a null field outright (400), so the HTTP route cannot
    reach the service layer with one. This test covers the second layer: the
    underlying function, called directly with the field SET to None (built
    via model_construct to bypass the edge validator), must leave an opted-in
    member opted in rather than writing bool(None) = False. Without it, any
    future caller that skips the pydantic layer silently revokes consent.
    """
    import duotypes as t
    from service.person import patch_profile_info

    p = make_person(name='NullPatch')
    with api_tx() as tx:
        set_spotlight_opt_in(tx, p['id'], True)

    req = t.PatchProfileInfo.model_construct(
        _fields_set={'spotlight_opt_in'}, spotlight_opt_in=None)
    s = t.SessionInfo(email='nullpatch@ahavah-test.invalid', session_token_hash='x',
                      person_id=p['id'], person_uuid=p['uuid'], signed_in=True,
                      pending_club_name=None)
    patch_profile_info(req, s)

    assert _flag(p['id'])['spotlight_opt_in'] is True
