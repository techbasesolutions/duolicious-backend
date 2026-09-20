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
ORIGIN = 'https://ahavah.app'

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
    smtp_module._ASSET_CACHE.clear()
    yield
    smtp_module._ASSET_CACHE.clear()


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


def test_the_related_part_names_its_root_media_type(mailer):
    """RFC 2387 makes `type` a required parameter of multipart/related. Most
    clients infer the root from the first part; older Outlook and some
    gateways are documented to care."""
    raw = _send(mailer, f'<img src="cid:{PNG}"/>')
    assert 'multipart/related' in raw
    assert 'type="multipart/alternative"' in raw


def test_an_asset_that_cannot_be_read_falls_back_to_the_remote_url(mailer):
    """The plan's invariant, on the mailer as well as the template: a missing
    asset is today's behaviour, never a dead image. A cid with no part behind
    it renders as a broken box, which is worse than the remote url it
    replaced."""
    raw = _send(mailer, '<img src="cid:not-a-real-asset.png"/>')
    assert 'multipart/related' not in raw
    assert 'Content-ID' not in raw
    assert f'src="{ORIGIN}/email/not-a-real-asset.png"' in raw
    assert 'cid:not-a-real-asset.png' not in raw


def test_a_readable_asset_beside_a_missing_one_still_goes_inline(mailer):
    raw = _send(mailer, f'<img src="cid:{PNG}"/><img src="cid:nope.png"/>')
    assert f'Content-ID: <{PNG}>' in raw
    assert 'Content-ID: <nope.png>' not in raw
    assert f'src="cid:{PNG}"' in raw
    assert f'src="{ORIGIN}/email/nope.png"' in raw


def test_a_cid_in_member_text_is_not_treated_as_an_asset(mailer):
    """emails/feedback.py interpolates a member's free text into the body. A
    member writing a file name must not attach a 35 KB image, and a word like
    "acid:" must not be read as a reference at all."""
    raw = _send(
        mailer,
        '<p>they wrote cid:badge-instagram.png, and acid:logo-horizontal.png</p>',
    )
    assert 'multipart/related' not in raw
    assert 'Content-ID' not in raw
    assert 'cid:badge-instagram.png' in raw   # member text is left alone


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


def test_a_transient_read_failure_is_not_cached(monkeypatch):
    """Only successes are worth caching. One OSError must not unbrand every
    email this worker sends until the container restarts."""
    import pathlib

    real = pathlib.Path.read_bytes
    attempts: list[int] = []

    def flaky(self):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError('disk gone')
        return real(self)

    monkeypatch.setattr('pathlib.Path.read_bytes', flaky)
    assert smtp_module._load_email_asset(PNG) is None
    assert smtp_module._load_email_asset(PNG) is not None


def test_the_mailer_and_the_templates_read_the_same_assets_folder():
    """Two independent guesses at the layout would fail silently and totally:
    base.py would emit cid: for everything while the mailer loaded nothing."""
    import emails.base

    assert smtp_module.EMAIL_ASSETS_DIR.resolve() == emails.base.ASSETS_DIR.resolve()


def test_every_cid_a_template_can_ask_for_is_loadable_by_the_mailer():
    """asset_src() decides a name travels inline. The mailer has to be able
    to load exactly that name. One walk over the bundled set binds the two,
    with no database and no send."""
    from emails.base import ASSETS_DIR, asset_src

    bundled = sorted(p.name for p in ASSETS_DIR.glob('*.png'))
    assert bundled, 'emails/assets/ holds no png'
    for name in bundled:
        assert asset_src(name) == f'cid:{name}'
        assert smtp_module._load_email_asset(name), name


# --- the Resend https path (what production uses) --------------------------

class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = '') -> None:
        self.status_code = status_code
        self.text = text

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


@pytest.fixture
def resend_recording(monkeypatch):
    """An Smtp on the Resend path that keeps every payload it posted, and can
    be told what status to answer with, call by call."""
    monkeypatch.setattr(smtp_module, 'USE_RESEND_API', True)
    monkeypatch.setattr(smtp_module, 'RESEND_FROM_OVERRIDE', '')
    posts: list[dict] = []
    statuses: list[int] = []

    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        # A snapshot, not the live dict: the retry edits the payload in place
        # and requests serialises it at post time either way.
        posts.append(dict(json or {}))
        status = statuses.pop(0) if statuses else 200
        return _FakeResponse(status, 'attachments: invalid')

    monkeypatch.setattr(requests, 'post', fake_post)
    return Smtp('host', 25, 'user', 're_key'), posts, statuses


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


def test_a_missing_asset_leaves_the_remote_url_in_the_resend_html(resend):
    """service/campaigns/outbox.py stores rendered html and the cron drains
    it later, possibly from a different deploy. An asset renamed between the
    two must not leave a dead cid in the message that actually ships."""
    mailer, posted = resend
    mailer._try_send(
        subject='s', body='<img src="cid:nope.png"/>', to_addr='to@example.com')
    assert posted['json']['html'] == f'<img src="{ORIGIN}/email/nope.png"/>'


def test_a_resend_rejection_of_the_attachments_still_sends_the_email(
        resend_recording):
    """The one new failure mode that could take out every outbound email in
    the product. A rejected payload drops the images, puts the urls back and
    posts again, so the email still arrives, unbranded at worst."""
    mailer, posts, statuses = resend_recording
    statuses.append(422)

    got = mailer._try_send(
        subject='s', body=f'<img src="cid:{PNG}"/>', to_addr='to@example.com')

    assert got == 'msg-1'
    assert len(posts) == 2
    assert 'attachments' in posts[0]
    assert 'attachments' not in posts[1]
    assert posts[1]['html'] == f'<img src="{ORIGIN}/email/{PNG}"/>'
    assert posts[1]['subject'] == 's'
    assert posts[1]['to'] == ['to@example.com']


def test_a_resend_rejection_with_no_attachments_is_not_retried(resend_recording):
    """The fallback exists for the attachments key alone. A genuine failure
    stays a failure, so Smtp.send's own retry and logging still apply."""
    mailer, posts, statuses = resend_recording
    statuses.append(422)

    with pytest.raises(Exception):
        mailer._try_send(
            subject='s', body='<p>hello</p>', to_addr='to@example.com')
    assert len(posts) == 1


def test_a_resend_rejection_that_survives_the_retry_still_raises(
        resend_recording):
    mailer, posts, statuses = resend_recording
    statuses.extend([422, 500])

    with pytest.raises(Exception):
        mailer._try_send(
            subject='s', body=f'<img src="cid:{PNG}"/>', to_addr='to@example.com')
    assert len(posts) == 2


# --- the templates ask for inline assets -----------------------------------

def test_the_shell_logo_asks_for_an_inline_part():
    from emails.base import render

    html = render(title='t', preheader='p', body_html='<p>b</p>', footer_html='f')
    assert f'src="cid:{PNG}"' in html
    assert 'https://ahavah.app/email/logo-horizontal-wht.png' not in html
    assert 'alt="Ahavah"' in html


def test_both_title_variants_ask_for_an_inline_part():
    """The dark variant is what keeps a headline visible in a dark-mode
    client, so it has to travel inline too or the swap needs the network."""
    from emails.base import title_image

    html = title_image('title-otp.png', 'title-otp-wht.png', 'Your sign-in code.', 528)
    assert 'src="cid:title-otp.png"' in html
    assert 'src="cid:title-otp-wht.png"' in html
    assert html.count('alt="Your sign-in code."') == 2


def test_an_asset_that_is_not_bundled_keeps_the_https_url():
    """The fallback is today's behaviour, never a broken image and never a
    raise."""
    from emails.base import asset_src, title_image

    html = title_image('title-nope.png', 'title-nope-wht.png', 'x', 528)
    assert 'src="https://ahavah.app/email/title-nope.png"' in html
    assert 'src="https://ahavah.app/email/title-nope-wht.png"' in html
    assert asset_src('title-nope.png') == 'https://ahavah.app/email/title-nope.png'


def test_a_cache_busting_query_still_resolves_to_the_bundled_file():
    """referral_intro.py appends ?v=2. A cid has no query string, so the
    name before it is what names the part."""
    from emails.base import asset_src

    assert asset_src(f'{PNG}?v=2') == f'cid:{PNG}'


def test_the_social_badges_travel_inside_the_message():
    """referral_community.py built its badge urls by hand, so the Instagram,
    Threads and Facebook glyphs stayed remote and stayed blank for exactly
    the reader this work exists for."""
    from emails.referral_community import referral_community_html

    html = referral_community_html('someone@example.org', 'abc123')
    for name in ('badge-instagram.png', 'badge-threads.png', 'badge-facebook.png'):
        assert f'src="cid:{name}"' in html
    assert f'{ORIGIN}/email/badge-' not in html


def test_a_sign_in_code_email_carries_its_branding_inside_the_message(mailer):
    """End to end on the real template: the email that reached the owner
    unbranded on 2026-09-20."""
    from service.person.template import otp_template

    raw = _send(mailer, otp_template('123456'))
    assert 'multipart/related' in raw
    for name in (PNG, 'title-otp.png', 'title-otp-wht.png'):
        assert f'Content-ID: <{name}>' in raw
    assert 'https://ahavah.app/email/' not in raw
