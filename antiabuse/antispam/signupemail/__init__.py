from antiabuse.antispam.signupemail.sql import *
from database import api_tx
from pathlib import Path

dot_insignificant_email_domains = set([
    "gmail.com",
    "googlemail.com",
])

plus_address_domains = set([
    "fastmail.com",
    "fastmail.fm",
    "gmail.com",
    "googlemail.com",
    "hotmail.co.uk",
    "hotmail.com",
    "hotmail.de",
    "hotmail.fr",
    "icloud.com",
    "live.com",
    "outlook.com",
    "pm.me",
    "proton.me",
    "protonmail.com",
    "zoho.com",
    "zohomail.com",
])

def check_and_update_bad_domains(email):
    _, domain = _split_one_at(email)
    if not domain:
        # No @ at all — treat as unknown/disposable so the caller errors.
        return False

    params = dict(email=email, domain=domain)

    # Check if we already know about the email domain
    with api_tx() as tx:
        domain_status = tx.execute(
            Q_EMAIL_INFO,
            params=params
        ).fetchone()['domain_status']

    if domain_status == 'registered':
        return True
    elif domain_status == 'unregistered-good':
        return True
    elif domain_status == 'unregistered-bad':
        return False
    elif domain_status == 'unregistered-unknown':
        return False
    else:
        raise Exception('Unhandled domain status')

def _split_one_at(email: str) -> tuple[str, str]:
    """Safely split on the LAST '@' so a malformed local-part containing
    '@' doesn't raise ValueError. Also strips surrounding whitespace.
    Empty / no-@ inputs return ('', '')."""
    e = (email or "").strip()
    if "@" not in e:
        return ("", "")
    name, _, domain = e.rpartition("@")
    return (name.lower(), domain.lower())


def normalize_email_dots(email: str) -> str:
    name, domain = _split_one_at(email)
    if not domain or domain not in dot_insignificant_email_domains:
        return email.lower().strip() if email else email
    name = name.replace('.', '')
    return f'{name}@{domain}'

def normalize_email_pluses(email: str) -> str:
    name, domain = _split_one_at(email)
    if not domain or domain not in plus_address_domains:
        return email.lower().strip() if email else email
    name, *_ = name.split('+')
    return f'{name}@{domain}'

def normalize_email_domain(email: str) -> str:
    name, domain = _split_one_at(email)
    if not domain or domain != 'googlemail.com':
        return email.lower().strip() if email else email
    return f'{name}@gmail.com'


def normalize_email(email: str) -> str:
    """Lowercase + normalize Gmail/Outlook dot/plus aliasing, idn-encode
    the domain so Cyrillic / homoglyph spoofs of allow-listed domains
    cannot be smuggled through (audit Auth #3). Safe on multi-@ / empty
    inputs — never raises ValueError."""
    if not email:
        return email
    email = normalize_email_dots(email)
    email = normalize_email_pluses(email)
    email = normalize_email_domain(email)

    # IDN/homoglyph defense: encode the domain part to ASCII (Punycode)
    # so allow-list comparisons against e.g. "ahavah.app" can't be
    # bypassed by an attacker submitting "ahavah.app" with a Cyrillic
    # 'a'. .encode('idna') raises UnicodeError on invalid domains; we
    # fall back to the lowercased original so the caller can decide
    # what to do with a malformed value (the route's validation layer
    # will reject it as not a valid EmailStr anyway).
    name, domain = _split_one_at(email)
    if not domain:
        return email
    try:
        ascii_domain = domain.encode('idna').decode('ascii')
    except (UnicodeError, UnicodeDecodeError):
        return email
    return f'{name}@{ascii_domain}'
