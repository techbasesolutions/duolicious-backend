"""
F11/F12: pendingdeletion hard delete must stage CDN cleanup queues and
purge referral rows.

F11: self-service hard deletion never fed undeleted_photo/undeleted_audio,
the only queues the CDN cleaners read, so a deleted member's photos stayed
publicly fetchable forever.

F12 (0037 regression): referral.inviter_email lost its FK/cascade to
beta_signup in migration 0037 (inviters can now be any member, not just
beta testers), and invitee_email never had one. Hard delete must purge
both directions explicitly or a deleted member's email lingers in
`referral` forever (and can block re-signup via UNIQUE(invitee_email)).
"""
from __future__ import annotations

import asyncio

from database import api_tx
from service.cron.pendingdeletion import hard_delete_expired_once


def test_hard_delete_stages_media_and_purges_referrals(make_person):
    p = make_person(name='Leaver', gender='Woman')
    with api_tx() as tx:
        email = tx.execute('SELECT email FROM person WHERE id = %(p)s',
                            dict(p=p['id'])).fetchone()['email']
        # Defensive: a prior failed run of this test can leak these
        # plain-text-keyed referral rows (they have no FK back to person
        # to clean them up automatically).
        tx.execute("DELETE FROM referral WHERE invitee_email IN "
                   "('ghost-invitee@example.org') OR inviter_email IN "
                   "('ghost-inviter@example.org')")
        tx.execute(
            "INSERT INTO photo (person_id, position, uuid, blurhash, hash) "
            "VALUES (%(p)s, 1, 'del-test-photo-uuid-1', '', 'del-test-hash-1')",
            dict(p=p['id']))
        tx.execute(
            "INSERT INTO audio (person_id, position, uuid) "
            "VALUES (%(p)s, 1, 'del-test-audio-uuid-1')",
            dict(p=p['id']))
        tx.execute(
            "INSERT INTO referral (inviter_email, invitee_email) VALUES "
            "(%(e)s, 'ghost-invitee@example.org'), "
            "('ghost-inviter@example.org', %(e)s)", dict(e=email))
        tx.execute(
            "UPDATE person SET activated = FALSE, "
            "deletion_requested_at = NOW() - INTERVAL '8 days' "
            "WHERE id = %(p)s", dict(p=p['id']))

    asyncio.run(hard_delete_expired_once())

    try:
        with api_tx() as tx:
            staged_photo = tx.execute(
                "SELECT 1 FROM undeleted_photo WHERE uuid = 'del-test-photo-uuid-1'"
            ).fetchone()
            assert staged_photo, \
                'hard delete must stage photo uuids for the CDN cleaner (F11)'

            staged_audio = tx.execute(
                "SELECT 1 FROM undeleted_audio WHERE uuid = 'del-test-audio-uuid-1'"
            ).fetchone()
            assert staged_audio, \
                'hard delete must stage audio uuids for the CDN cleaner (F11)'

            ref = tx.execute(
                'SELECT count(*) AS n FROM referral WHERE '
                'inviter_email = %(e)s OR invitee_email = %(e)s',
                dict(e=email)).fetchone()['n']
            assert ref == 0, 'referral rows survived hard delete (F12)'

            gone = tx.execute('SELECT 1 FROM person WHERE id = %(p)s',
                               dict(p=p['id'])).fetchone()
            assert gone is None
    finally:
        with api_tx() as tx:
            tx.execute("DELETE FROM undeleted_photo WHERE uuid = 'del-test-photo-uuid-1'")
            tx.execute("DELETE FROM undeleted_audio WHERE uuid = 'del-test-audio-uuid-1'")
            tx.execute("DELETE FROM referral WHERE invitee_email = 'ghost-invitee@example.org' "
                       "OR inviter_email = 'ghost-inviter@example.org'")
