"""The one process that actually sends email (F07).

Everything else queues: `run_campaign` writes outbox rows, the spotlight
routes write outbox rows inside their own transactions. This loop is the
single drain, so SMTP happens in exactly one place, on a schedule, with
retries and a visible failure state instead of inside a web request.

`drain` is a plain sync function (it opens short transactions in sequence and
makes a blocking SMTP call between them), so it runs on a thread here and the
cron event loop is never blocked. Same shape as
`service/cron/spotlightretention`.

The SMTP client is built ONLY when there is something to send: the poll runs
every 30 seconds, and constructing (and authenticating) a client on every
empty tick would be a needless connection to the mail provider all day.
"""
from __future__ import annotations

import asyncio
import os
import random

from database import api_tx
from service.campaigns import outbox
from service.cron.cronutil import print_stacktrace, MAX_RANDOM_START_DELAY, env_int

EMAIL_OUTBOX_POLL_SECONDS = env_int('DUO_CRON_EMAIL_OUTBOX_POLL_SECONDS', 30)

print(f'Hello from cron module: {__name__}')


def _has_work() -> bool:
    """Cheap due-row probe, on the (state, next_attempt_at) index. Also true
    when there is a reservation to reap, so a dead drain's rows still reach
    `acceptance_unknown` on a quiet queue."""
    with api_tx('read committed') as tx:
        row = tx.execute(
            """SELECT EXISTS (
                   SELECT 1 FROM email_outbox
                    WHERE (state = 'queued' AND next_attempt_at <= NOW())
                       OR (state = 'reserved'
                           AND reserved_at < NOW() - make_interval(secs => %(s)s))
               ) AS any_work""",
            dict(s=outbox.RESERVATION_TIMEOUT_SECONDS)).fetchone()
    return bool(row['any_work'])


def _drain_once() -> None:
    if not _has_work():
        return
    from smtp import make_aws_smtp
    out = outbox.drain(api_tx, make_aws_smtp())
    if out['reserved'] or out['unknown_reaped']:
        print(f'email_outbox: {out}')


async def email_outbox_forever() -> None:
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(lambda: asyncio.to_thread(_drain_once))
        await asyncio.sleep(EMAIL_OUTBOX_POLL_SECONDS)
