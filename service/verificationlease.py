"""How long a verification run is allowed to hold its row, and how many
times a dead one may be re-queued.

This lives in a leaf module because two very different callers need the same
two numbers: the cron that claims and reaps rows, and the admin System tab
that counts the ones nobody claimed. Importing the cron package from an API
route would pull in boto3, the R2 credentials and the OpenAI client for the
sake of two integers.

THE LEASE IS MEASURED, NOT CHOSEN.

A verification job makes exactly one outbound call, `verification.verify`,
and its cost is bounded by the call's own settings rather than by anything
we observe, because nothing anywhere records how long a classifier call
takes. The bound:

  * `verification/__init__.py` passes `timeout=45` to
    `AsyncOpenAI().chat.completions.create`, so no single HTTP attempt can
    run longer than 45 seconds.
  * The OpenAI SDK (3.16.2) retries a failed attempt `max_retries` times,
    and nothing here overrides the default of 2. So one `verify()` call is
    up to three HTTP attempts: 3 x 45 = 135 seconds.
  * Between attempts the SDK sleeps
    `min(INITIAL_RETRY_DELAY * 2 ** n, MAX_RETRY_DELAY)` with
    `INITIAL_RETRY_DELAY = 0.5` and `MAX_RETRY_DELAY = 8.0`, so 0.5 then
    1.0 second: 1.5 seconds of backoff.

  Ceiling for the work the lease has to cover: 135 + 1.5 = 136.5 seconds.

  Plus ten percent for connection setup, the two small transactions either
  side of the call, and the one second cron poll: 136.5 x 1.1 = 150.15,
  rounded up to the second.

151 seconds. The number matters in one direction only. Anything at or under
137 seconds reaps a job that is merely being retried by the SDK, sends the
member's selfie to the classifier a second time and pays for it twice, which
is the failure mode a reaper introduces and the reason this is derived
rather than picked. Erring long only delays a retry the member is already
waiting on.

The ceiling on retries is the house number: `service.campaigns.outbox` and
`service.spotlight.queue` both stop at three attempts. Two reaps is three
total attempts at the same selfie. A submission that dies three times is not
dying from bad luck, and each attempt is a paid vision call over up to eight
images, so the row stops being retried and starts being counted instead.

HOW MANY JOBS ONE TICK MAY START.

The picker had no bound, so a backlog that built up during an OpenAI outage
was handed to the worker in one listing and run sequentially inside a single
`for` loop. At the ceiling above that is 136.5 seconds a job, and for as
long as it lasts the worker never returns to the top of its loop and never
re-reads the queue.

Ten is the bound. It is picked so the ordinary tick is unaffected (a healthy
classifier call is seconds, and ten of them is still a short tick) while the
pathological one, every call exhausting its SDK retries, is capped at
10 x 136.5 = 1365 seconds before the worker re-lists. Re-listing costs one
indexed query, drops rows another worker has claimed since, and picks up
anything submitted meanwhile. It does not slow a backlog down: the poll is
one second and ORDER BY id keeps the queue fair across ticks, so a backlog
of any depth drains at the same rate either way. The bound buys
interruptibility, not throughput.

All three are settable from the environment so an operator can widen the
lease during an OpenAI incident, or drain a backlog faster, without a
deploy.
"""
from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    """Unset or blank reads as the default. Compose files pass every cron
    setting through as an empty string when it is not configured."""
    raw = os.environ.get(name, '')
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


VERIFICATION_LEASE_SECONDS = _env_int(
    'DUO_CRON_VERIFICATION_LEASE_SECONDS', 151)

VERIFICATION_MAX_REAPS = _env_int(
    'DUO_CRON_VERIFICATION_MAX_REAPS', 2)

VERIFICATION_MAX_JOBS_PER_TICK = _env_int(
    'DUO_CRON_VERIFICATION_MAX_JOBS_PER_TICK', 10)
