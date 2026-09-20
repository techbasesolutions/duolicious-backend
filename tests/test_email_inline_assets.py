"""The mailer attaches the brand images the html asks for.

Why this exists: a mail client decides per sender whether to fetch a remote
image. Every piece of Ahavah brand furniture in an email is a remote image,
so a sender the reader has not whitelisted gets an unbranded email: no logo,
no Ultra headline, body copy in a fallback font. That is what reached the
owner on 2026-09-20.

An image carried inside the message renders with no permission and no
network call. The mailer reads the `cid:` references out of the html it was
handed and attaches the matching file from emails/assets, on both the SMTP
path and the Resend HTTPS path that production actually uses.

The mailer carries every email in the product, so the hard rule these tests
hold is: nothing here may ever break a send. A body with no `cid:` produces
exactly the message it produced before this work, and an asset that cannot
be read is skipped, never raised.
"""
from __future__ import annotations

import re

import pytest

import smtp as smtp_module
from smtp import Smtp


PNG = 'logo-horizontal-wht.png'
PNG_DARK = 'logo-horizontal.png'

# Python picks a random multipart boundary per message, so two runs of the
# same code never match byte for byte. Normalise it and the comparison is
# about structure and headers, which is what "today's message" means.
_BOUNDARY = re.compile(r'={5,}\d+={2,}')


def _normalise(raw: str) -> str:
    return _BOUNDARY.sub('BOUNDARY', raw)


class _RecordingSmtp:
    """Stands in for smtplib.SMTP and keeps the flattened message."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def sendmail(self, *, from_addr: str, to_addrs: list[str], msg: str) -> None:
        self.sent.append({'from_addr': from_addr, 'to_addrs': to_addrs, 'msg': msg})

    def quit(self) -> None:
        """Smtp.__del__ closes the connection; without this the teardown of
        every test in this file prints a stack of noise."""


@pytest.fixture
def mailer(monkeypatch):
    """An Smtp on the smtplib path with a stub socket."""
    monkeypatch.setattr(smtp_module, 'USE_RESEND_API', False)
    s = Smtp('host', 25, 'user', 'pass')
    s._smtp = _RecordingSmtp()
    return s


@pytest.fixture(autouse=True)
def _clear_asset_cache():
    smtp_module._load_email_asset.cache_clear()
    yield
    smtp_module._load_email_asset.cache_clear()


def _send(mailer, body: str) -> str:
    mailer._try_send(subject='s', body=body, to_addr='to@example.com')
    return mailer._smtp.sent[-1]['msg']


# --- the smtplib path ------------------------------------------------------

def test_two_cid_references_become_two_inline_parts(mailer):
    body = f'<img src="cid:{PNG}"/><img src="cid:{PNG_DARK}"/>'
    raw = _send(mailer, body)

    assert 'multipart/related' in raw
    assert 'multipart/alternative' in raw
    assert f'Content-ID: <{PNG}>' in raw
    assert f'Content-ID: <{PNG_DARK}>' in raw
    assert raw.count('Content-Disposition: inline') == 2
    assert raw.count('Content-Type: image/png') == 2


def test_the_same_asset_twice_is_attached_once(mailer):
    raw = _send(mailer, f'<img src="cid:{PNG}"/><img src="cid:{PNG}"/>')
    assert raw.count(f'Content-ID: <{PNG}>') == 1


def test_a_body_with_no_cid_reference_produces_todays_message(mailer):
    """The mailer carries every email in the product. An email that has no
    inline asset must come out exactly as it did before this work."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from service.config import EMAIL_DOMAIN, PRODUCT_NAME

    body = '<p>hello</p><img src="https://ahavah.app/email/logo-horizontal.png"/>'
    raw = _send(mailer, body)

    todays = MIMEMultipart('alternative')
    todays['From'] = f'{PRODUCT_NAME} <no-reply@{EMAIL_DOMAIN}>'
    todays['To'] = 'to@example.com'
    todays['Subject'] = 's'
    todays.attach(MIMEText(body, 'html'))

    assert _normalise(raw) == _normalise(todays.as_string())


def test_the_optional_headers_survive_an_inline_send(mailer):
    mailer._try_send(
        subject='s',
        body=f'<img src="cid:{PNG}"/>',
        to_addr='to@example.com',
        reply_to='human@example.com',
        list_unsubscribe='<mailto:unsub@example.com>',
    )
    raw = mailer._smtp.sent[-1]['msg']
    assert 'Reply-To: human@example.com' in raw
    assert 'List-Unsubscribe: <mailto:unsub@example.com>' in raw
    assert 'List-Unsubscribe-Post: List-Unsubscribe=One-Click' in raw


def test_an_asset_that_cannot_be_read_is_skipped_and_the_mail_still_goes(mailer):
    raw = _send(mailer, '<img src="cid:not-a-real-asset.png"/>')
    assert 'multipart/related' not in raw
    assert 'not-a-real-asset.png' in raw     # the body is untouched
    assert 'Content-ID' not in raw


def test_a_readable_asset_beside_a_missing_one_still_goes_inline(mailer):
    raw = _send(mailer, f'<img src="cid:{PNG}"/><img src="cid:nope.png"/>')
    assert f'Content-ID: <{PNG}>' in raw
    assert 'Content-ID: <nope.png>' not in raw


def test_a_cid_cannot_reach_outside_the_assets_folder():
    for name in ('../../requirements.txt', '/etc/passwd', '..'):
        assert smtp_module._load_email_asset(name) is None


def test_asset_loading_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError('disk gone')

    monkeypatch.setattr('pathlib.Path.read_bytes', boom)
    assert smtp_module._load_email_asset(PNG) is None
    assert smtp_module._inline_email_assets(f'<img src="cid:{PNG}"/>') == []


def test_an_asset_is_read_once_per_process(monkeypatch):
    import pathlib

    reads: list[str] = []
    real = pathlib.Path.read_bytes

    def counting(self):
        reads.append(self.name)
        return real(self)

    monkeypatch.setattr('pathlib.Path.read_bytes', counting)
    for _ in range(3):
        smtp_module._inline_email_assets(f'<img src="cid:{PNG}"/>')
    assert reads.count(PNG) == 1


# --- the Resend https path (what production uses) --------------------------

class _FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {'id': 'msg-1'}


@pytest.fixture
def resend(monkeypatch):
    """An Smtp on the Resend path, with the posted payload captured."""
    monkeypatch.setattr(smtp_module, 'USE_RESEND_API', True)
    monkeypatch.setattr(smtp_module, 'RESEND_FROM_OVERRIDE', '')
    posted: dict = {}

    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        posted['url'] = url
        posted['json'] = json
        return _FakeResponse()

    monkeypatch.setattr(requests, 'post', fake_post)
    return Smtp('host', 25, 'user', 're_key'), posted


def test_the_resend_payload_carries_the_attachments_in_the_documented_shape(resend):
    """Field names read from Resend's own API reference:
    https://resend.com/docs/api-reference/emails/send-email and
    https://resend.com/docs/dashboard/emails/embed-inline-images
    `attachments[]` takes `content` (base64), `filename`, `content_type`
    and `content_id`, and the html points at `cid:<content_id>` with no
    angle brackets around the id."""
    import base64

    mailer, posted = resend
    mailer._try_send(
        subject='s', body=f'<img src="cid:{PNG}"/>', to_addr='to@example.com')

    attachments = posted['json']['attachments']
    assert len(attachments) == 1
    one = attachments[0]
    assert set(one) == {'content', 'filename', 'content_type', 'content_id'}
    assert one['filename'] == PNG
    assert one['content_id'] == PNG          # no angle brackets, matches cid:
    assert one['content_type'] == 'image/png'
    assert base64.b64decode(one['content'])[:8] == b'\x89PNG\r\n\x1a\n'


def test_the_resend_payload_is_unchanged_when_the_body_has_no_cid(resend):
    mailer, posted = resend
    mailer._try_send(subject='s', body='<p>hello</p>', to_addr='to@example.com')
    assert 'attachments' not in posted['json']
    assert set(posted['json']) == {'from', 'to', 'subject', 'html'}


def test_the_resend_send_still_returns_the_message_id_with_attachments(resend):
    mailer, posted = resend
    got = mailer._try_send(
        subject='s', body=f'<img src="cid:{PNG}"/>', to_addr='to@example.com')
    assert got == 'msg-1'


def test_a_missing_asset_does_not_stop_the_resend_send(resend):
    mailer, posted = resend
    got = mailer._try_send(
        subject='s', body='<img src="cid:nope.png"/>', to_addr='to@example.com')
    assert got == 'msg-1'
    assert 'attachments' not in posted['json']
