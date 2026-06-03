"""
betareengagement - sends the re-engagement email to beta testers who joined
the cohort >=7 days ago but still have empty waitlist demographics.

Rationale: a beta tester is someone who tapped "Count me in" on the waitlist
completion screen. Ideally they completed the demographic wizard first, but
the home email-only form + the legacy "already in" short-circuit meant some
of them landed in beta_signup without any answers in waitlist_signup. Without
those answers we cannot line matches up for them at launch.

Selection (per tick):
  - beta_signup row exists and is >= BETA_REENGAGEMENT_GRACE_DAYS old
  - their waitlist_signup row is missing OR answers IS NULL OR answers = '{}'
  - reengagement_sent_at IS NULL (so we send at most once per cohort row)
  - email is not a sample address (@example.com — already filtered by the
    underlying send helper, double-belt-and-braces here for visibility)

Mutation: send the re-engagement email (best-effort), then stamp
beta_signup.reengagement_sent_at = NOW() so the row is excluded next tick.

Manual one-off send remains available via:
    python -m emails.send_reengagement --only EMAIL
"""

from database.asyncdatabase import api_tx
from service.cron.cronutil import print_stacktrace, MAX_RANDOM_START_DELAY
import asyncio
import os
import random

from emails.base import mask_email, suppressed_sql_pattern
from emails.reengagement import send_reengagement


# Poll cadence — every 6 hours is plenty for a 7-day trigger. The exact
# moment a row crosses the threshold doesn't matter; what matters is that
# we never miss it and never spam.
BETA_REENGAGEMENT_POLL_SECONDS = int(os.environ.get(
    'DUO_CRON_BETA_REENGAGEMENT_POLL_SECONDS',
    str(60 * 60 * 6),  # 6 hours
))

BETA_REENGAGEMENT_GRACE_DAYS = int(os.environ.get(
    'DUO_BETA_REENGAGEMENT_GRACE_DAYS',
    '7',
))

print(f'Hello from cron module: {__name__}')


_Q_PICK = """
    SELECT b.email
      FROM beta_signup b
      LEFT JOIN waitlist_signup w ON w.email = b.email
     WHERE b.reengagement_sent_at IS NULL
       AND b.created_at < NOW() - (%(days)s || ' days')::INTERVAL
       AND (w.answers IS NULL OR w.answers = '{}'::jsonb)
       AND NOT (b.email ILIKE ANY(%(suppressed)s))
     ORDER BY b.created_at
     LIMIT 100
"""

_Q_MARK_SENT = """
    UPDATE beta_signup
       SET reengagement_sent_at = NOW()
     WHERE email = %(email)s
"""

# Outbox-pattern claim: stamp the row BEFORE the SMTP call so a process
# restart / concurrent tick / scaled-out cron cannot double-send. Returns
# the email iff this tick won the claim; another tick that already
# claimed it gets an empty result and skips (audit DI #7).
_Q_CLAIM = """
    UPDATE beta_signup
       SET reengagement_sent_at = NOW()
     WHERE email = %(email)s
       AND reengagement_sent_at IS NULL
    RETURNING email
"""


async def send_beta_reengagement_once():
    """Find eligible beta testers and send one re-engagement email each."""
    async with api_tx() as tx:
        cur = await tx.execute(
            _Q_PICK,
            dict(
                days=BETA_REENGAGEMENT_GRACE_DAYS,
                suppressed=suppressed_sql_pattern(),
            ),
        )
        rows = await cur.fetchall()

    if not rows:
        return

    print(f'beta_reengagement: {len(rows)} candidate(s) to email')
    for row in rows:
        email = row['email']
        masked = mask_email(email)

        # Outbox claim first — stamp atomically so a concurrent tick or
        # process restart cannot re-send this row even if our SMTP call
        # crashes mid-flight. Trade-off: a Resend HTTPS failure leaves
        # the row stamped and we won't retry, which is acceptable for a
        # low-criticality nudge (audit DI #7).
        async with api_tx() as tx:
            claim = (await tx.execute(_Q_CLAIM, dict(email=email))).fetchone()
        if not claim:
            # Another tick already claimed it (or row got stamped between
            # _Q_PICK and now). Skip.
            print(f'beta_reengagement: skip (already claimed) {masked}')
            continue

        try:
            # send_reengagement is sync (smtp + best-effort). Push to a
            # thread so we don't block the cron event loop on SMTP latency.
            await asyncio.to_thread(send_reengagement, email)
            print(f'beta_reengagement: sent {masked}')
        except Exception as e:
            print(f'beta_reengagement: send failed (already claimed) for {masked}: {e!r}')


async def send_beta_reengagement_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(send_beta_reengagement_once)
        await asyncio.sleep(BETA_REENGAGEMENT_POLL_SECONDS)
