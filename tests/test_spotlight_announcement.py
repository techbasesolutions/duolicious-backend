import pytest

from database import api_tx
from emails.spotlight_announcement import spotlight_announcement_html, SUBJECT
from emails.send_spotlight_announcement import build_for
from service.campaigns.runner import run_campaign


def test_announcement_is_on_template_and_has_no_em_dash():
    em_dash = chr(0x2014)  # built at runtime so the source file carries no literal em dash
    html = spotlight_announcement_html('https://ahavah.app/spotlight/confirm/t', 'https://ahavah.app/settings/privacy', 'https://ahavah.app/u/x')
    assert 'title-spotlight.png' in html and 'title-spotlight-wht.png' in html
    assert 'https://ahavah.app/spotlight/confirm/t' in html
    assert 'Unsubscribe' in html
    assert em_dash not in html and em_dash not in SUBJECT
    assert 'first name, age, country' in html.lower()


def test_build_for_raises_for_a_deactivated_recipient(make_person):
    p = make_person(name='Deactivated')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = FALSE WHERE id = %(id)s", dict(id=p['id']))
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
    with pytest.raises(ValueError, match='no_person'):
        build_for(dict(person_id=p['id'], email=email, name='Deactivated'))


def test_deactivated_recipient_produces_no_send_and_a_counted_failure(make_person, monkeypatch):
    """The email is a non-suppressed `ahavah-test.invalid` address (not the
    `make_person` fixture's default `@example.com`) so this test actually
    exercises build_for's `no_person` guard rather than run_campaign's
    unrelated suppression check, which would otherwise skip an
    `@example.com` recipient before build_for ever runs and report
    `error=None` regardless of the fix under test."""
    import service.campaigns.runner as r
    sent = []

    class _S:
        def send(self, **kw):
            sent.append(kw)
            return 'mid'
    monkeypatch.setattr(r, 'make_aws_smtp', lambda: _S())
    p = make_person(name='Deactivated2')
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'deactivated-{p["id"]}@ahavah-test.invalid', id=p['id']))
        tx.execute("UPDATE person SET activated = FALSE WHERE id = %(id)s", dict(id=p['id']))
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
    result = run_campaign(
        api_tx, 'e1', 'no-person-e1', [dict(person_id=p['id'], email=email, name='Deactivated2')],
        build_for, send=True, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert result['sent'] == 0 and result['error'] == 'no_person'
    assert sent == []
    assert not any('href="None"' in s['body'] for s in sent)
