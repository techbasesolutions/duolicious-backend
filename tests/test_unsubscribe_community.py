"""Coverage for the `community` unsubscribe scope (E2 weekly email).

No unsubscribe test file existed before this scope; this file also locks in
the GET/POST split on /u/<token>: GET only renders the confirmation page
(mail scanners follow GET links and must not silently unsubscribe people),
only POST (the RFC 8058 one-click endpoint, and a human confirming on the
page) stamps the column.
"""
from __future__ import annotations

from database import api_tx
from service.unsubscribe import make_token, stamp_unsubscribed


def _email_of(person_id: int) -> str:
    with api_tx() as tx:
        return tx.execute(
            "SELECT email FROM person WHERE id = %(id)s", dict(id=person_id)
        ).fetchone()['email']


def _community_unsubscribed_at(person_id: int):
    with api_tx() as tx:
        return tx.execute(
            "SELECT community_unsubscribed_at FROM person WHERE id = %(id)s",
            dict(id=person_id),
        ).fetchone()['community_unsubscribed_at']


def test_community_scope_stamps_person_column(make_person):
    p = make_person(name='Unsub')
    email = _email_of(p['id'])

    with api_tx() as tx:
        ok = stamp_unsubscribed(tx, 'community', email)

    assert ok
    assert _community_unsubscribed_at(p['id']) is not None


def test_get_unsubscribe_renders_without_stamping_then_post_stamps(client, make_person):
    p = make_person(name='GetPost')
    email = _email_of(p['id'])
    token = make_token('community', email)

    r = client.get(f'/u/{token}')
    assert r.status_code == 200
    assert _community_unsubscribed_at(p['id']) is None, \
        'GET must only render the confirmation page; mail scanners follow GET links'

    r2 = client.post(f'/u/{token}')
    assert r2.status_code == 200
    assert _community_unsubscribed_at(p['id']) is not None
