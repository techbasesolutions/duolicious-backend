"""Durable email outbox (F07) with visible uncertainty (F08).

Before this module, every campaign and transactional email was an SMTP call
made inside the request or script that decided to send it: an api restart
mid-run lost whatever had not gone out, a transient SMTP error lost the
message entirely, and a caller that retried could send twice. The outbox
splits that into two halves that can each fail independently:

  * ENQUEUE writes one row, in the SAME transaction as whatever decided to
    send (the candidate the invite belongs to, the receipt the "your card is
    live" email belongs to). The unique key (campaign, campaign_id,
    person_id) is now the idempotency point, so a retried request adds
    nothing rather than sending a second copy.

  * DRAIN reserves due rows in one transaction, talks to SMTP OUTSIDE any
    transaction, and records the outcome in another. SMTP is slow and can
    hang; holding a database transaction across it is what turned a mail
    hiccup into a lock pile-up.

F08 is the part that is easy to get wrong: a process that dies between
`smtp.send` returning and the acceptance being written leaves a row whose
real fate nobody knows. Re-sending it would double mail a member; dropping
it would silently lose the message. Neither is honest, so the reaper moves
such a row to `acceptance_unknown` and leaves it there for a human. That
state is surfaced by the admin status endpoint rather than swept up.

Nothing here nests a transaction: every function takes the transaction (or
raw connection) its caller already holds, and `drain` opens short ones in
sequence via the factory it is handed.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from emails.base import is_suppressed_send, mask_email
from service.campaigns import campaign_unsubscribed, can_send, log_send

# Backoff applied AFTER attempt n fails, indexed by n - 1. An attempt that
# takes the row to MAX_ATTEMPTS is terminal: it goes to 'failed' instead of
# being requeued, so the third entry is only reached if MAX_ATTEMPTS is
# raised. Deliberately short: these are transient SMTP errors, and a member
# waiting on a card-ready invite should not wait hours for the retry.
RETRY_BACKOFF_SECONDS = (60, 600, 3600)
MAX_ATTEMPTS = 3

# A row reserved longer than this had its drain die mid-flight (see F08 in
# the module docstring). Comfortably longer than any single SMTP call, so a
# merely slow send is never reaped out from under itself.
RESERVATION_TIMEOUT_SECONDS = 600

BATCH = 50

STATES = ('queued', 'reserved', 'accepted', 'acceptance_unknown', 'failed', 'skipped')

# Named post-send effects. A hook runs in the SAME transaction as the
# acceptance it belongs to, so `person.reinvite_sent_at` can never be stamped
# for a message that was not actually accepted by SMTP (the old
# `run_campaign(post_send=...)` callable stamped it around the send instead).
# A NAME is stored rather than a callable because the row outlives the
# process that wrote it: a pickled function could not survive a restart,
# which is the whole point of the outbox.
POST_SEND_HOOKS = {
    'reinvite_sent_at': lambda tx, person_id: tx.execute(
        "UPDATE person SET reinvite_sent_at = NOW() WHERE id = %(p)s", dict(p=person_id)),
}

_Q_ENQUEUE = """
    INSERT INTO email_outbox (campaign, campaign_id, person_id, email, payload, exempt, unsub_scope)
    VALUES (%(c)s, %(cid)s, %(pid)s, %(email)s, %(payload)s, %(exempt)s, %(scope)s)
    ON CONFLICT (campaign, campaign_id, person_id) DO NOTHING
    RETURNING id
"""

# The inner SELECT takes the row locks with SKIP LOCKED, so a second drain
# running at the same moment simply does not see the rows this one is about
# to reserve -- it never blocks on them, and never reserves them twice.
_Q_RESERVE = """
    UPDATE email_outbox
       SET state = 'reserved', reserved_at = NOW(), attempts = attempts + 1
     WHERE id IN (
        SELECT id FROM email_outbox
         WHERE state = 'queued' AND next_attempt_at <= NOW()
         ORDER BY id
         LIMIT %(n)s
         FOR UPDATE SKIP LOCKED
     )
    RETURNING *
"""

_Q_REAP = """
    UPDATE email_outbox
       SET state = 'acceptance_unknown', last_error = 'reservation expired'
     WHERE state = 'reserved'
       AND reserved_at < NOW() - make_interval(secs => %(s)s)
"""

_Q_ACCEPT = """
    UPDATE email_outbox
       SET state = 'accepted', sent_at = NOW(), provider_message_id = %(mid)s, last_error = NULL
     WHERE id = %(id)s
    RETURNING person_id, campaign, campaign_id, payload
"""

_Q_SKIP = """
    UPDATE email_outbox SET state = 'skipped', last_error = %(reason)s WHERE id = %(id)s
"""

_Q_REQUEUE = """
    UPDATE email_outbox
       SET state = 'queued', reserved_at = NULL, last_error = %(err)s,
           next_attempt_at = NOW() + make_interval(secs => %(backoff)s)
     WHERE id = %(id)s
"""

_Q_FAIL = """
    UPDATE email_outbox
       SET state = 'failed', reserved_at = NULL, last_error = %(err)s
     WHERE id = %(id)s
"""

_Q_STATUS = """
    SELECT state, count(*) AS n FROM email_outbox
     WHERE campaign = %(c)s AND campaign_id = %(cid)s
     GROUP BY state
"""


def enqueue(tx, *, campaign: str, campaign_id: str, person_id: int, email: str,
            subject: str, html: str, from_addr: str, unsub_scope: str,
            list_unsubscribe: Optional[str] = None, exempt: bool = False,
            cap_days: int = 7, post_send: Optional[str] = None) -> Optional[int]:
    """Queue one message. Returns the new row id, or None when this exact
    (campaign, campaign_id, person_id) was already queued, sent, skipped or
    failed -- which is what makes a retried caller safe.

    Call this inside the transaction that decided to send, never around it:
    the invite and the candidate it belongs to either both commit or neither
    does.

    `post_send` is a KEY of POST_SEND_HOOKS, not a callable (see the note
    there). `cap_days` rides along so the drain's own frequency re-check uses
    the campaign's real window rather than the 7-day default -- E2's weekly
    cadence is a 6-day cap, and re-checking it at 7 would hold back exactly
    the member the 6-day window exists to reach."""
    if post_send is not None and post_send not in POST_SEND_HOOKS:
        raise ValueError(f'unknown post_send hook: {post_send}')
    payload = dict(subject=subject, html=html, from_addr=from_addr,
                   list_unsubscribe=list_unsubscribe, post_send=post_send,
                   cap_days=int(cap_days))
    row = tx.execute(_Q_ENQUEUE, dict(c=campaign, cid=campaign_id, pid=person_id, email=email,
                                      payload=json.dumps(payload), exempt=bool(exempt),
                                      scope=unsub_scope)).fetchone()
    return row['id'] if row else None


def reserve(tx, limit: int = BATCH) -> list[dict]:
    """Claim up to `limit` due rows for this drain and return them in full.

    `tx` may be an `api_tx` transaction OR a raw psycopg connection -- both
    expose `.execute` -- exactly as `lock_request_rows` in
    service/spotlight/queue.py does, so a test can hold two independent
    reservations open on separate connections and prove the SKIP LOCKED
    behaviour rather than assuming it."""
    return tx.execute(_Q_RESERVE, dict(n=limit)).fetchall()


def reap_reserved(tx) -> int:
    """Move every reservation older than RESERVATION_TIMEOUT_SECONDS to
    `acceptance_unknown` and return how many. These are NOT retried: see the
    F08 paragraph in the module docstring."""
    return tx.execute(_Q_REAP, dict(s=RESERVATION_TIMEOUT_SECONDS)).rowcount


def mark_accepted(tx, id: int, provider_message_id: Optional[str]) -> None:
    """The message was accepted by SMTP. Records the acceptance, writes the
    frequency-cap send log, and runs the row's post-send hook -- all three in
    this one transaction, so none of them can be true without the others."""
    row = tx.execute(_Q_ACCEPT, dict(id=id, mid=str(provider_message_id) if provider_message_id else None)).fetchone()
    if not row:
        return
    log_send(tx, row['person_id'], row['campaign'], row['campaign_id'],
             str(provider_message_id) if provider_message_id else None)
    hook_name = (row['payload'] or {}).get('post_send')
    if hook_name:
        hook = POST_SEND_HOOKS.get(hook_name)
        if hook is None:
            # Should be unreachable: enqueue validates the name. A row
            # written by an older deploy whose hook has since been removed
            # lands here, and losing the side effect is better than losing
            # the acceptance record of a message that really did go out.
            print(f'email_outbox: unknown post_send hook {hook_name!r} on row {id}')
        else:
            hook(tx, row['person_id'])


def mark_skipped(tx, id: int, reason: str) -> None:
    """A send-time re-check refused this row: 'suppressed', 'unsubscribed'
    or 'capped'. Terminal, and deliberately distinct from 'failed' -- nothing
    went wrong, we simply must not send it."""
    tx.execute(_Q_SKIP, dict(id=id, reason=reason))


def mark_failed_attempt(tx, id: int, error: str, attempts: int) -> None:
    """SMTP refused (or blew up). Requeue on the backoff while attempts are
    left, otherwise park the row in 'failed' where the admin status endpoint
    can see it. `attempts` is the row's count AFTER `reserve` incremented
    it, i.e. the number of attempts made so far."""
    err = (error or '')[:2000]
    if attempts < MAX_ATTEMPTS:
        backoff = RETRY_BACKOFF_SECONDS[min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
        tx.execute(_Q_REQUEUE, dict(id=id, err=err, backoff=backoff))
    else:
        tx.execute(_Q_FAIL, dict(id=id, err=err))


def _refusal(tx, row: dict) -> Optional[str]:
    """The send-time re-checks, in the order they cost. None means send.

    These run again here, not only at enqueue time, because the gap between
    the two can be long: a member may have unsubscribed, been mailed by
    another campaign, or had their address suppressed since the row was
    written."""
    payload = row['payload'] or {}
    if is_suppressed_send(row['email']):
        return 'suppressed'
    if campaign_unsubscribed(tx, row['person_id'], row['unsub_scope']):
        return 'unsubscribed'
    if not can_send(tx, row['person_id'], row['campaign'], row['campaign_id'],
                    cap_days=int(payload.get('cap_days') or 7), exempt=bool(row['exempt'])):
        return 'capped'
    return None


def drain(tx_factory, smtp, limit: int = BATCH) -> dict:
    """Send up to `limit` messages. Returns dict(reserved, accepted, skipped,
    failed, unknown_reaped).

    Transaction shape, which is the whole point of this function: the reaper
    runs once, in its own transaction, at the start. Then each message gets
    FOUR short transactions of its own -- reserve it, re-check it, (SMTP with
    no transaction open), record the outcome -- and only then is the next one
    reserved.

    Reserving ONE row per iteration rather than the whole batch up front is
    deliberate. A drain that dies part way through (deploy, OOM, container
    kill) leaves every row it had already claimed stuck in 'reserved' until
    the reaper times them out, and every one of those is a message whose fate
    nobody can be sure of. Claiming them one at a time means at most the
    single message actually in flight is stranded; everything else is still
    'queued' and the next drain simply picks it up.

    `smtp.send` must never run inside a transaction -- it is a network round
    trip to a third party, and holding row locks across it is how a mail
    outage becomes a database outage."""
    with tx_factory() as tx:
        unknown_reaped = reap_reserved(tx)

    reserved = accepted = skipped = failed = 0
    while reserved < limit:
        with tx_factory() as tx:
            claimed = reserve(tx, limit=1)
        if not claimed:
            break
        row = claimed[0]
        reserved += 1
        payload = row['payload'] or {}
        with tx_factory() as tx:
            reason = _refusal(tx, row)
            if reason is not None:
                mark_skipped(tx, row['id'], reason)
        if reason is not None:
            skipped += 1
            print(f"email_outbox: skipped {row['campaign']} for {mask_email(row['email'])} ({reason})")
            continue
        try:
            message_id = smtp.send(subject=payload.get('subject'), body=payload.get('html'),
                                   to_addr=row['email'], from_addr=payload.get('from_addr'),
                                   list_unsubscribe=payload.get('list_unsubscribe'))
        except Exception as e:      # noqa: BLE001 -- any SMTP failure is a retryable attempt
            with tx_factory() as tx:
                mark_failed_attempt(tx, row['id'], str(e), int(row['attempts']))
            failed += 1
            print(f"email_outbox: attempt {row['attempts']} failed for {mask_email(row['email'])}: {e}")
            continue
        with tx_factory() as tx:
            mark_accepted(tx, row['id'], message_id)
        accepted += 1
        print(f"email_outbox: sent {row['campaign']} to {mask_email(row['email'])}")

    return dict(reserved=reserved, accepted=accepted, skipped=skipped, failed=failed,
                unknown_reaped=unknown_reaped)


def status(tx, campaign: str, campaign_id: str) -> dict[str, Any]:
    """Counts per state for one run, every state present even at zero so the
    admin dashboard never has to guess whether a missing key means zero or a
    state it does not know about.

    A state this build has never heard of (a row written by a newer deploy
    during a rollout, say) is bucketed under `other` rather than raising: the
    dashboard losing the label is a far smaller problem than the dashboard
    500ing. The `other` key only appears when there is something in it, so
    the normal shape stays the six states above."""
    out: dict[str, Any] = {s: 0 for s in STATES}
    for r in tx.execute(_Q_STATUS, dict(c=campaign, cid=campaign_id)).fetchall():
        if r['state'] in out:
            out[r['state']] = int(r['n'])
        else:
            out['other'] = out.get('other', 0) + int(r['n'])
    return out
