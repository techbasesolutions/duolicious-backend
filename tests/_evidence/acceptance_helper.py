"""Database-side helper for the 2026-09-16 local acceptance run.

The end-to-end probe (`ahavah-admin/tests/_evidence/acceptance-local-probe.mjs`)
drives the real admin worker, the real API over HTTP and a real browser. The
few things it cannot do through a public route (make a synthetic member, mint
an admin session, read back rows to check what happened, drain the mail
outbox with a stub SMTP client) go through this script instead, one JSON
answer per invocation.

Not a test: pytest never collects this directory. It only ever talks to the
disposable local test database (DUO_DB_HOST from the test compose file). It
never dials SMTP, the object store or any platform.

Run inside the acceptance API container, for example:

    docker exec ahavah-acceptance-api python tests/_evidence/acceptance_helper.py member Probe

Subcommands:
    member NAME [GENDER]      eligible, opted-in, verified, activated member with
                              one approved photo and a signed-in session
    admin                     admin member with a signed-in session
    query SQL [PARAMS_JSON]   rows as JSON
    exec SQL [PARAMS_JSON]    rowcount
    drain PERSON_ID...        drain the outbox for these people with a stub SMTP
    drain-die PERSON_ID       reserve and "send" that person's next queued row,
                              then kill this process before the acceptance lands
    reap-now PERSON_ID...     backdate those people's reservations past the
                              timeout and run one drain with a stub SMTP
    attribute PERSON_ID REF   attribute_signup exactly as finish-onboarding calls it
    post-stats REQUEST_KEY    service.growth.queries.post_stats
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from uuid import uuid4

from database import api_tx


def _out(value) -> None:
    print(json.dumps(value, default=str))


def _session(person_id: int, email: str) -> str:
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
               VALUES (%(h)s, %(e)s, %(p)s, TRUE, '123456')""",
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=person_id))
    return tok


def _person(name: str, gender: str = 'Woman') -> dict:
    email = f'acceptance-probe-{uuid4()}@ahavah-test.invalid'
    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO person (
                email, normalized_email, name, date_of_birth,
                coordinates, gender_id, about,
                location_short_friendly, location_long_friendly, unit_id
            )
            VALUES (
                %(email)s, %(email)s, %(name)s, '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender WHERE name = %(gender)s),
                'about', 'Somewhere', 'Somewhere, Nowhere',
                (SELECT id FROM unit LIMIT 1)
            )
            RETURNING id, uuid::text AS uuid, email
            """,
            dict(email=email, name=name, gender=gender)).fetchone()
    return dict(row)


def cmd_member(name: str, gender: str = 'Woman') -> None:
    p = _person(name, gender)
    photo = str(uuid4())
    with api_tx() as tx:
        tx.execute(
            """UPDATE person SET activated = TRUE, spotlight_opt_in = TRUE, spotlight_opt_in_at = NOW(),
                      ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01',
                      deletion_requested_at = NULL, spotlight_last_featured_at = NULL,
                      sign_up_time = NOW()
                WHERE id = %(id)s""", dict(id=p['id']))
        tx.execute(
            """INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
               VALUES (%(u)s, %(id)s, 1, 'approved', 'probeblurhash', gen_random_uuid()::text)""",
            dict(u=photo, id=p['id']))
    p['photo_uuid'] = photo
    p['token'] = _session(p['id'], p['email'])
    _out(p)


def cmd_admin() -> None:
    p = _person('AcceptanceAdmin', 'Man')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = TRUE, roles = ARRAY['admin']::TEXT[] WHERE id = %(i)s",
                   dict(i=p['id']))
    p['token'] = _session(p['id'], p['email'])
    _out(p)


def cmd_query(sql: str, params: str = '{}') -> None:
    with api_tx('read committed') as tx:
        rows = tx.execute(sql, json.loads(params)).fetchall()
    _out([dict(r) for r in rows])


def cmd_exec(sql: str, params: str = '{}') -> None:
    with api_tx() as tx:
        n = tx.execute(sql, json.loads(params)).rowcount
    _out(dict(rowcount=n))


class _StubSmtp:
    def __init__(self, die_for: int | None = None):
        self.sent: list[dict] = []
        self.die_for = die_for

    def send(self, **kw):
        if self.die_for is not None:
            # The message "left" this process: nothing after this line runs,
            # so the acceptance write can never land.
            print(json.dumps(dict(dying_during_send_to=kw.get('to_addr'))), flush=True)
            os._exit(9)
        self.sent.append(dict(to=kw.get('to_addr'), subject=kw.get('subject')))
        return f'stub-{len(self.sent)}'


def _park_others(person_ids: list[int]) -> None:
    with api_tx() as tx:
        tx.execute(
            """UPDATE email_outbox SET next_attempt_at = NOW() + interval '1 hour'
                WHERE state = 'queued' AND NOT (person_id = ANY(%(ids)s))""",
            dict(ids=person_ids))


def cmd_drain(*person_ids: str) -> None:
    from service.campaigns import outbox
    ids = [int(p) for p in person_ids]
    _park_others(ids)
    smtp = _StubSmtp()
    result = outbox.drain(api_tx, smtp)
    _out(dict(result=result, sent=smtp.sent))


def cmd_drain_die(person_id: str) -> None:
    from service.campaigns import outbox
    _park_others([int(person_id)])
    outbox.drain(api_tx, _StubSmtp(die_for=int(person_id)))
    _out(dict(unreachable=True))


def cmd_reap_now(*person_ids: str) -> None:
    from service.campaigns import outbox
    ids = [int(p) for p in person_ids]
    with api_tx() as tx:
        n = tx.execute(
            """UPDATE email_outbox SET reserved_at = NOW() - interval '2 hours'
                WHERE state = 'reserved' AND person_id = ANY(%(ids)s)""",
            dict(ids=ids)).rowcount
    _park_others(ids)
    smtp = _StubSmtp()
    result = outbox.drain(api_tx, smtp)
    _out(dict(backdated=n, result=result, sent=smtp.sent))


def cmd_attribute(person_id: str, ref: str) -> None:
    """The exact call finish-onboarding makes (service/person post_finish_
    onboarding), in its own transaction, for a person the probe created."""
    from service.spotlight.attribution import attribute_signup
    with api_tx() as tx:
        credited = attribute_signup(tx, int(person_id), ref)
        stamped = tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s",
                             dict(p=int(person_id))).fetchone()['spotlight_ref']
    _out(dict(credited=credited, spotlight_ref=stamped))


def cmd_post_stats(request_key: str) -> None:
    from service.growth.queries import post_stats
    with api_tx('read committed') as tx:
        _out(post_stats(tx, request_key))


COMMANDS = {
    'member': cmd_member, 'admin': cmd_admin, 'query': cmd_query, 'exec': cmd_exec,
    'drain': cmd_drain, 'drain-die': cmd_drain_die, 'reap-now': cmd_reap_now,
    'attribute': cmd_attribute, 'post-stats': cmd_post_stats,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    COMMANDS[sys.argv[1]](*sys.argv[2:])
