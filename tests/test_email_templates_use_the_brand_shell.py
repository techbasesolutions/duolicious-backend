"""Every Ahavah email is built from the canonical shell in emails/base.py.

Why this test exists: on 2026-09-19 an email template (emails/reinvite.py)
was changed to draw its own headline with a styled <h1>, because the brand
title image for that email said something that was no longer true. Inline
type carries one colour, while every brand title image ships a light and a
dark variant, so in a dark-mode client the headline rendered near-black on
the near-black card and vanished. Members received an email that did not
look like Ahavah.

The rule the owner has given repeatedly: no UI, including an email, is ever
hand rolled. A surface uses an existing brand asset or waits for one to be
designed. These tests make that shortcut fail in CI instead of in an inbox.
They check the shape of the templates, never their wording, so new emails
and new copy stay free.
"""
from __future__ import annotations

import ast
import pathlib
import re

EMAILS_DIR = pathlib.Path(__file__).resolve().parent.parent / 'emails'

# The brand images the API attaches to a message. A mail client decides per
# sender whether to fetch a remote image, so an asset the API can read and
# send inline is the only branding that always arrives.
ASSETS_DIR = EMAILS_DIR / 'assets'

# base.py owns the shell (render, title_image, button, chip, callout). The
# senders (send_*.py) pick recipients and never build markup.
_NOT_TEMPLATES = {'base.py', '__init__.py'}

# Templates that already drew their own display type before this guard existed
# (measured 2026-09-19). Debt, not permission: an entry goes when that email
# is next redesigned, and a second test fails if a listed file turns out to be
# clean, so the list cannot rot.
_KNOWN_HAND_ROLLED = {
    'community_invite.py', 'feedback.py', 'marriage_checklist.py',
    'new_features.py', 'profile_nudge.py', 'referral_community.py',
    'waitlist_admin.py',
}

# Staff-only mail, outside the member brand shell by design.
_STAFF_ONLY = {'feedback.py', 'waitlist_admin.py'}

# Large type that is DATA, not design: the six sign-in code digits change with
# every email, so they cannot be an image. Named one by one, with the reason,
# so the exception cannot quietly widen.
_BIG_TYPE_ALLOWED = {
    'service/person/template/__init__.py':
        'the six sign-in code boxes: the code differs per email, so it cannot be an image',
}

# A heading tag, or a font size large enough to be acting as one. Either way
# it is display type, which belongs in an Ultra title image.
_HEADING_TAG = re.compile(r'<\s*h[1-4]\b', re.I)
_BIG_FONT = re.compile(r'font-size:\s*(\d{2,})px')
_DISPLAY_PX = 24  # body copy in this system is 14 to 19px; 24+ is display type


# Email templates also live outside emails/: the sign-in code, the dormancy
# email and the message notifier each keep theirs beside the code that sends
# it. The guard missed them until 2026-09-20, which is exactly how an
# off-brand email reaches an inbox unnoticed.
_OUTSIDE_TEMPLATE_DIRS = (
    EMAILS_DIR.parent / 'service' / 'person' / 'template',
    EMAILS_DIR.parent / 'service' / 'cron' / 'autodeactivate2' / 'template',
    EMAILS_DIR.parent / 'service' / 'cron' / 'notifications' / 'template',
)


def _template_files() -> list[pathlib.Path]:
    files = [p for p in EMAILS_DIR.glob('*.py')
             if p.name not in _NOT_TEMPLATES and not p.name.startswith('send_')]
    for directory in _OUTSIDE_TEMPLATE_DIRS:
        init = directory / '__init__.py'
        # A template module outside emails/ counts only when it actually
        # builds markup; service/cron/notifications/template just makes
        # strings and urls for emails/notification.py.
        if init.is_file() and '<' in init.read_text(encoding='utf-8'):
            files.append(init)
    return sorted(files)


def test_the_guard_actually_sees_the_templates_outside_the_emails_folder():
    """A list that silently finds nothing is worse than no list."""
    found = {p for p in _template_files() if p.parent.name == 'template'}
    assert found, 'no template module outside emails/ was picked up'
    assert any('person' in str(p) for p in found), 'the sign-in code template is missing'


def test_every_member_facing_email_is_sent_from_the_same_address():
    """A mail client decides whether to load remote images per sender, and
    every piece of Ahavah branding in an email is a remote image. The
    sign-in code went out from its own noreply-otp@ address and reached the
    owner unbranded, in a fallback font, while campaign mail from support@
    rendered correctly (2026-09-20)."""
    senders = {}
    pattern = re.compile(r"""from_addr=f?["']([^"']*@\{?EMAIL_DOMAIN\}?)""")
    roots = [EMAILS_DIR, EMAILS_DIR.parent / 'service']
    for root in roots:
        for path in root.rglob('*.py'):
            if '__pycache__' in str(path):
                continue
            for addr in pattern.findall(path.read_text(encoding='utf-8')):
                senders.setdefault(addr, []).append(path.name)
    stray = {a: f for a, f in senders.items()
             if not a.startswith('support@')
             # Staff-only mail keeps its own addresses on purpose.
             and not a.startswith(('waitlist@', 'feedback@'))}
    assert not stray, ('These send from an address members never see elsewhere, so their '
                       f'images may be blocked: {stray}')


def _repo_relative(path: pathlib.Path) -> str:
    return path.relative_to(EMAILS_DIR.parent).as_posix()


def _hand_rolled(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding='utf-8')
    found = []
    if _HEADING_TAG.search(text):
        found.append('heading tag in the markup')
    if _repo_relative(path) not in _BIG_TYPE_ALLOWED:
        for size in sorted({int(m) for m in _BIG_FONT.findall(text)}):
            if size >= _DISPLAY_PX:
                found.append(f'inline font-size {size}px, display type')
    return found


def test_the_big_type_allowance_names_files_that_exist():
    for rel in _BIG_TYPE_ALLOWED:
        assert (EMAILS_DIR.parent / rel).is_file(), f'{rel} is allowed big type but is gone'


def test_no_new_template_hand_rolls_a_headline():
    offenders = []
    for path in _template_files():
        if path.name in _KNOWN_HAND_ROLLED:
            continue
        offenders += [f'{path.name}: {why}' for why in _hand_rolled(path)]
    assert not offenders, (
        'These templates draw their own display type, which disappears in a '
        'dark-mode client. Use title_image() with an existing brand title from '
        'ahavah-web/public/email, or commission one through a design brief:\n  '
        + '\n  '.join(offenders))


def test_the_hand_rolled_list_has_no_stale_entries():
    fixed = [name for name in sorted(_KNOWN_HAND_ROLLED)
             if (EMAILS_DIR / name).is_file() and not _hand_rolled(EMAILS_DIR / name)]
    assert not fixed, ('These templates no longer hand roll display type. Remove '
                       'them from _KNOWN_HAND_ROLLED:\n  ' + '\n  '.join(fixed))


def test_every_template_renders_through_the_shell():
    """A template that calls render() is inside the brand shell. One that
    builds its own document is not."""
    offenders = []
    for path in _template_files():
        if path.name in _STAFF_ONLY:
            continue
        text = path.read_text(encoding='utf-8')
        if '<html' in text.lower():
            offenders.append(f'{path.name}: builds its own document')
        if 'def ' in text and 'render(' not in text and 'emailtemplate' not in text:
            offenders.append(f'{path.name}: never calls render()')
    assert not offenders, (
        'These templates bypass emails/base.py render():\n  ' + '\n  '.join(offenders))


def test_every_title_image_names_a_file_that_exists_in_both_variants():
    """A title image that 404s leaves a blank where the headline should be,
    which is how a hand-rolled heading comes to look necessary. Both variants
    must exist: the dark one keeps the headline visible in dark mode.

    The manifest is the list of files in emails/assets, checked in here so
    this runs in CI where only this repo is mounted. scripts/sync_email_assets.sh
    copies the images out of ahavah-web/public/email and regenerates it, so
    the two lists are the same set after a sync. Run it after adding an email
    image:

        ./scripts/sync_email_assets.sh
    """
    manifest_path = pathlib.Path(__file__).resolve().parent / 'email_assets_manifest.txt'
    available = {line.strip() for line in manifest_path.read_text(encoding='utf-8').splitlines()
                 if line.strip()}
    assert available, 'email_assets_manifest.txt is empty'

    web_email_dir = EMAILS_DIR.parent.parent / 'ahavah-web' / 'public' / 'email'
    if web_email_dir.is_dir():
        on_disk = {p.name for p in web_email_dir.glob('*.png')}
        assert on_disk == available, (
            'tests/email_assets_manifest.txt is out of date. Regenerate it '
            f'(missing from manifest: {sorted(on_disk - available)}; '
            f'listed but gone: {sorted(available - on_disk)})')

    missing = []
    call = re.compile(r'title_image\(\s*"([^"]+)"\s*,\s*"([^"]+)"')
    for path in _template_files():
        for light, dark in call.findall(path.read_text(encoding='utf-8')):
            for name in (light, dark):
                # referral_intro.py cache-busts with "?v=2"; the file is the
                # part before the query string.
                if name.split('?', 1)[0] not in available:
                    missing.append(f'{path.name}: {name}')
    assert not missing, ('Title images referenced but absent from '
                         'ahavah-web/public/email:\n  ' + '\n  '.join(missing))


def _manifest_names() -> set[str]:
    manifest_path = pathlib.Path(__file__).resolve().parent / 'email_assets_manifest.txt'
    return {line.strip() for line in manifest_path.read_text(encoding='utf-8').splitlines()
            if line.strip()}


def test_the_brand_assets_ship_inside_the_api_image():
    """The API cannot attach what it cannot read.

    emails/assets/ is the copy the running container reads at send time, so
    the inline branding survives a client that blocks remote images. Resync
    with scripts/sync_email_assets.sh.
    """
    assert ASSETS_DIR.is_dir(), (
        'emails/assets/ is missing. Run scripts/sync_email_assets.sh to copy '
        'the brand images out of ahavah-web/public/email.')
    bundled = {p.name for p in ASSETS_DIR.glob('*.png')}
    assert bundled, 'emails/assets/ holds no png'


def test_the_bundled_assets_and_the_manifest_agree():
    """One list, two repos. A drift here means an email asks for a cid the
    mailer cannot load, and the branding silently falls back to a remote url."""
    available = _manifest_names()
    bundled = {p.name for p in ASSETS_DIR.glob('*.png')} if ASSETS_DIR.is_dir() else set()
    assert bundled == available, (
        'emails/assets/ and tests/email_assets_manifest.txt disagree. Run '
        'scripts/sync_email_assets.sh '
        f'(in the manifest but not bundled: {sorted(available - bundled)}; '
        f'bundled but not in the manifest: {sorted(bundled - available)})')


def test_no_template_builds_a_brand_image_url_by_hand():
    """asset_src() in base.py is the one switch between an image that travels
    inside the message and one the reader's client has to be willing to fetch.
    A template that assembles the url itself opts that image out of the
    branding this repo exists to guarantee, and it does it silently."""
    offenders = sorted(
        p.name for p in EMAILS_DIR.glob('*.py')
        if p.name != 'base.py'
        and 'EMAIL_ASSET_ORIGIN' in p.read_text(encoding='utf-8'))
    assert not offenders, (
        'These reach for EMAIL_ASSET_ORIGIN directly, so their images stay '
        'remote and stay blank for a reader whose client blocks them. Use '
        'asset_src() from emails/base.py:\n  ' + '\n  '.join(offenders))


def test_every_asset_a_template_asks_for_is_bundled():
    """A title image or logo referenced by a template but absent from
    emails/assets/ cannot go inline, so that email loses its headline in any
    client that blocks remote images."""
    bundled = {p.name for p in ASSETS_DIR.glob('*.png')} if ASSETS_DIR.is_dir() else set()
    wanted = set()
    call = re.compile(r'title_image\(\s*"([^"]+)"\s*,\s*"([^"]+)"')
    for path in _template_files():
        for light, dark in call.findall(path.read_text(encoding='utf-8')):
            wanted.update(name.split('?', 1)[0] for name in (light, dark))
    base_text = (EMAILS_DIR / 'base.py').read_text(encoding='utf-8')
    wanted.update(re.findall(r'/email/([A-Za-z0-9._-]+\.png)', base_text))
    missing = sorted(wanted - bundled)
    assert not missing, (
        'These assets are referenced by a template but are not in '
        'emails/assets/, so they cannot be sent inline:\n  ' + '\n  '.join(missing))


def test_templates_are_syntactically_whole():
    for path in _template_files():
        ast.parse(path.read_text(encoding='utf-8'))
