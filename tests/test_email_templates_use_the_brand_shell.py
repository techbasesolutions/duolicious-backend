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

# A heading tag, or a font size large enough to be acting as one. Either way
# it is display type, which belongs in an Ultra title image.
_HEADING_TAG = re.compile(r'<\s*h[1-4]\b', re.I)
_BIG_FONT = re.compile(r'font-size:\s*(\d{2,})px')
_DISPLAY_PX = 24  # body copy in this system is 14 to 19px; 24+ is display type


def _template_files() -> list[pathlib.Path]:
    return sorted(p for p in EMAILS_DIR.glob('*.py')
                  if p.name not in _NOT_TEMPLATES and not p.name.startswith('send_'))


def _hand_rolled(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding='utf-8')
    found = []
    if _HEADING_TAG.search(text):
        found.append('heading tag in the markup')
    for size in sorted({int(m) for m in _BIG_FONT.findall(text)}):
        if size >= _DISPLAY_PX:
            found.append(f'inline font-size {size}px, display type')
    return found


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

    The manifest is the list of files in ahavah-web/public/email, checked in
    here so this runs in CI where only this repo is mounted. Regenerate after
    adding an email image:

        ls ../ahavah-web/public/email/*.png | xargs -n1 basename | sort > tests/email_assets_manifest.txt
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


def test_templates_are_syntactically_whole():
    for path in _template_files():
        ast.parse(path.read_text(encoding='utf-8'))
