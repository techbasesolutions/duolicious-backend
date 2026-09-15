"""Single-use token nonces bound to the member's consent epoch (Wave 1
remediation, F11).

Confirm (E1) and card (E4) tokens used to be stateless HMACs: anyone who
still had an old link could replay it after the member withdrew. Task 1's
`spotlight_token_nonce` table plus `person.spotlight_consent_epoch` let a
token be checked against exactly one still-live consent window --
`issue_nonce` stamps the nonce with the epoch current at mint time,
`check_nonce` compares it against the epoch current at use time, and
`consume_nonce` makes the first successful use burn it. `withdraw_member`
(Task 4) bumps the epoch and marks every unused nonce for the member used,
so any token minted before a withdrawal reads back 'stale' even before its
own TTL would have expired it.
"""
from __future__ import annotations

import secrets

_PURPOSES = ('confirm', 'card')


def issue_nonce(tx, person_id: int, purpose: str) -> str:
    if purpose not in _PURPOSES:
        raise ValueError('bad_purpose')
    # Checked explicitly rather than trusting the INSERT ... SELECT below to
    # fail loudly: with no matching person row, that SELECT simply returns
    # zero rows and the INSERT silently inserts nothing, handing the caller
    # back a nonce string that was never persisted.
    if not tx.execute("SELECT 1 FROM person WHERE id = %(pid)s", dict(pid=person_id)).fetchone():
        raise ValueError('not_found')
    nonce = secrets.token_urlsafe(16)
    tx.execute(
        """INSERT INTO spotlight_token_nonce (nonce, person_id, purpose, epoch)
           SELECT %(n)s, %(pid)s, %(purpose)s, spotlight_consent_epoch
             FROM person WHERE id = %(pid)s""",
        dict(n=nonce, pid=person_id, purpose=purpose))
    return nonce


def check_nonce(tx, nonce: str, person_id: int, purpose: str) -> str:
    """'ok' (row exists for this person/purpose, unused, epoch current) |
    'used' (used_at set and epoch current) | 'stale' (epoch stale, used or
    not) | 'invalid' (no row, other person, or other purpose)."""
    row = tx.execute(
        """SELECT n.used_at, n.epoch, p.spotlight_consent_epoch AS current_epoch
             FROM spotlight_token_nonce n
             JOIN person p ON p.id = n.person_id
            WHERE n.nonce = %(n)s AND n.person_id = %(pid)s AND n.purpose = %(purpose)s""",
        dict(n=nonce, pid=person_id, purpose=purpose)).fetchone()
    if not row:
        return 'invalid'
    if row['epoch'] != row['current_epoch']:
        return 'stale'
    return 'used' if row['used_at'] is not None else 'ok'


def consume_nonce(tx, nonce: str) -> bool:
    cur = tx.execute(
        "UPDATE spotlight_token_nonce SET used_at = NOW() WHERE nonce = %(n)s AND used_at IS NULL",
        dict(n=nonce))
    return cur.rowcount == 1
