"""Wave 2 acceptance probe: the harness behind the "Dry runs" section of
`docs/superpowers/plans/2026-09-15-spotlight-wave-2-evidence.md`.

It exercises `outbox.drain` and `cleanup.run_cleanup_batch` against a stub
SMTP client and a stub storage delete function, printing verbatim results, so
the evidence document quotes a live demonstration rather than an assertion.
Not a test and never collected by pytest (this directory holds no test_*.py);
it is committed (fix wave item 9) so the next person to run the acceptance
matrix reproduces the same numbers instead of rebuilding the harness from the
document's prose.

NO NETWORK. `smtp` is a local stub object and `delete` is a local callable, so
neither SMTP nor the object store is ever dialled -- which matters because the
test environment carries non-blank R2 credentials pointing at nothing
reachable. The only thing it talks to is the test database.

It cleans up every row it creates: the two probe people, their outbox rows and
its own cleanup jobs, in `finally` blocks, so a failed run leaves no more
behind than a successful one. `run_cleanup_batch` is scoped with
`target_prefix` to the probe's own keys, so a pending job belonging to
something else is never reserved or deleted.

Run it inside the test stack:

    MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm \\
      -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 \\
      --entrypoint bash api -c \\
      "cd /app && PYTHONPATH=/app python tests/_evidence/wave2-dry-run-probe.py"
"""
from __future__ import annotations

import json
from uuid import uuid4

from database import api_tx
from service.campaigns import outbox
from service.spotlight import cleanup
from service.spotlight.queue import set_setting


def make_person(email: str) -> int:
    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO person (
                email, normalized_email, name, date_of_birth,
                coordinates, gender_id, about,
                location_short_friendly, location_long_friendly, unit_id
            )
            VALUES (
                %(email)s, %(email)s, 'Wave2Probe', '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender WHERE name = 'Man'),
                'about', 'Somewhere', 'Somewhere, Nowhere',
                (SELECT id FROM unit LIMIT 1)
            )
            RETURNING id
            """,
            dict(email=email),
        ).fetchone()
        return row['id']


class StubSmtp:
    def __init__(self):
        self.sent = []

    def send(self, **kw):
        self.sent.append(kw)
        return 'stub-mid-1'


def park_other_due_rows(keep_person_ids: list[int]) -> list[int]:
    """Push every OTHER due outbox row an hour out and return their ids, so
    `outbox.drain` below can only reach this probe's own rows.

    `drain` has no filter of its own (production has one outbox and one cron),
    so without this the probe would drain whatever the shared, long-lived test
    database happened to have queued and mark it `accepted` against a stub
    SMTP. That is not read-only, and it made tests that had left rows queued
    fail on the next suite run. `unpark` puts them back. The cleanup batch has
    `target_prefix` for the same purpose and needs no equivalent."""
    with api_tx() as tx:
        ids = [r['id'] for r in tx.execute(
            """UPDATE email_outbox SET next_attempt_at = NOW() + interval '1 hour'
                WHERE state = 'queued' AND next_attempt_at <= NOW()
                  AND NOT (person_id = ANY(%(keep)s))
             RETURNING id""",
            dict(keep=list(keep_person_ids))).fetchall()]
    return ids


def unpark(ids: list[int]) -> None:
    """Restore rows `park_other_due_rows` pushed out. They were due when it
    ran, so NOW() is the faithful value to put back."""
    if not ids:
        return
    with api_tx() as tx:
        tx.execute("UPDATE email_outbox SET next_attempt_at = NOW() WHERE id = ANY(%(ids)s)",
                   dict(ids=list(ids)))


def probe_outbox():
    email = f'wave2-probe-{uuid4()}@ahavah-test.invalid'
    pid = make_person(email)
    pid2 = None
    parked: list[int] = []
    try:
        with api_tx() as tx:
            new_id = outbox.enqueue(
                tx, campaign='e1', campaign_id='wave2-evidence-run', person_id=pid,
                email=email, subject='Wave 2 evidence probe', html='<p>hi</p>',
                from_addr='hello@ahavah.app', unsub_scope='notifications')
        print('enqueue ->', new_id)

        parked += park_other_due_rows([pid])
        smtp = StubSmtp()
        result = outbox.drain(api_tx, smtp)
        print('drain (accepts one) ->', json.dumps(result))
        print('smtp.sent ->', json.dumps(smtp.sent))

        result2 = outbox.drain(api_tx, smtp)
        print('drain (nothing due) ->', json.dumps(result2))

        # acceptance_unknown: reserve directly, then age the reservation past
        # the timeout and drain again with a fresh smtp that must NOT be called.
        email2 = f'wave2-probe-{uuid4()}@ahavah-test.invalid'
        pid2 = make_person(email2)
        with api_tx() as tx:
            outbox.enqueue(tx, campaign='e1', campaign_id='wave2-evidence-lost', person_id=pid2,
                            email=email2, subject='s', html='<p>h</p>',
                            from_addr='hello@ahavah.app', unsub_scope='notifications')
        parked += park_other_due_rows([pid, pid2])
        with api_tx() as tx:
            outbox.reserve(tx)
        with api_tx() as tx:
            tx.execute("UPDATE email_outbox SET reserved_at = NOW() - interval '11 minutes' WHERE person_id = %(p)s",
                       dict(p=pid2))
        smtp3 = StubSmtp()
        result3 = outbox.drain(api_tx, smtp3)
        print('drain (reaps a lost reservation) ->', json.dumps(result3))
        print('smtp3.sent (must be empty) ->', json.dumps(smtp3.sent))
        with api_tx('read committed') as tx:
            row2 = tx.execute("SELECT state FROM email_outbox WHERE person_id = %(p)s", dict(p=pid2)).fetchone()
        print('lost row state ->', row2['state'])
    finally:
        unpark(parked)
        with api_tx() as tx:
            tx.execute("DELETE FROM email_outbox WHERE campaign_id IN ('wave2-evidence-run', 'wave2-evidence-lost')")
        with api_tx() as tx:
            tx.execute("DELETE FROM person WHERE id = ANY(%(p)s)",
                       dict(p=[i for i in (pid, pid2) if i is not None]))


def probe_cleanup():
    prefix = 'spotlight/wave2-evidence/'
    key_ok = f'{prefix}{uuid4()}-ok.png'
    key_bad = f'{prefix}{uuid4()}-bad.png'
    key_halt = f'{prefix}{uuid4()}-halt.png'
    try:
        with api_tx() as tx:
            cleanup.enqueue_asset_delete(tx, key_ok)
            cleanup.enqueue_asset_delete(tx, key_bad)

        def confirming_delete(keys):
            return [k for k in keys if k == key_ok]

        # target_prefix scopes the batch to this probe's own keys, exactly as
        # the test suite does, so an unrelated pending job elsewhere in the
        # (disposable, shared) test database is never touched.
        result = cleanup.run_cleanup_batch(api_tx, delete=confirming_delete, target_prefix=prefix)
        print('run_cleanup_batch (partial confirmation) ->', json.dumps(result))

        with api_tx('read committed') as tx:
            rows = tx.execute(
                "SELECT target, state, attempts FROM cleanup_job WHERE target IN (%(a)s, %(b)s)",
                dict(a=key_ok, b=key_bad)).fetchall()
        print('cleanup_job rows ->', json.dumps([dict(r) for r in rows]))

        # emergency stop: halt, no delete call, outstanding counted.
        with api_tx() as tx:
            cleanup.enqueue_asset_delete(tx, key_halt)
            set_setting(tx, 'external_access_enabled', 'false')
        try:
            calls = []
            halted = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: calls.append(keys) or [],
                                                target_prefix=prefix)
            print('run_cleanup_batch (halted) ->', json.dumps(halted))
            print('delete calls made while halted ->', calls)
        finally:
            with api_tx() as tx:
                set_setting(tx, 'external_access_enabled', 'true')

        # drain the retry so nothing is left pending, then clean the halted job.
        cleanup.run_cleanup_batch(api_tx, delete=lambda keys: list(keys), target_prefix=prefix)
    finally:
        with api_tx() as tx:
            tx.execute("DELETE FROM cleanup_job WHERE target IN (%(a)s, %(b)s, %(c)s)",
                       dict(a=key_ok, b=key_bad, c=key_halt))


if __name__ == '__main__':
    print('--- outbox.drain ---')
    probe_outbox()
    print('--- cleanup.run_cleanup_batch ---')
    probe_cleanup()
