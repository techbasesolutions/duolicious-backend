"""Sign-up attribution from campaign links (spec 3.4).

A Spotlight post's CTA is a `/s/<key>` campaign link (see
`service.campaigns.make_campaign_link` / `record_click`). When someone
clicks it, browses, and eventually signs up, `attribute_signup` closes the
loop: it stamps the new person with the click's key (`person.spotlight_ref`)
and marks the specific click that led to the sign-up
(`campaign_click.signup_person_id`), so `post_stats` below can report
clicks vs. signups per post.

Deliberately conservative:
  - Never overwrites an existing `person.spotlight_ref` -- a member's
    attribution is fixed at their first sign-up, not their latest click.
  - Only a non-bot click within the last 7 days can be credited, and only
    the most recent such click for that key -- an old or bot click is not
    good evidence of causing this particular sign-up.
  - An unknown, empty, missing, or over-long ref is a silent no-op: this
    runs on every finish-onboarding call, most of which carry no ref at
    all, and a malformed value must never fail the signup itself.

Runs entirely inside the caller's transaction (the same one that graduates
the onboardee into a person row) -- it never opens one of its own.
"""
from __future__ import annotations

from typing import Optional

MAX_REF_LEN = 32


def attribute_signup(tx, person_id: int, ref: Optional[str]) -> bool:
    if not ref or len(ref) > MAX_REF_LEN:
        return False

    known = tx.execute(
        "SELECT 1 FROM campaign_link WHERE key = %(k)s",
        dict(k=ref),
    ).fetchone()
    if not known:
        return False

    stamped = tx.execute(
        """
        UPDATE person
           SET spotlight_ref = %(ref)s
         WHERE id = %(pid)s AND spotlight_ref IS NULL
        RETURNING id
        """,
        dict(ref=ref, pid=person_id),
    ).fetchone()
    if not stamped:
        return False

    tx.execute(
        """
        UPDATE campaign_click
           SET signup_person_id = %(pid)s
         WHERE id = (
             SELECT id FROM campaign_click
              WHERE link_key = %(ref)s
                AND ua_class <> 'bot'
                AND signup_person_id IS NULL
                AND clicked_at > NOW() - interval '7 days'
              ORDER BY clicked_at DESC
              LIMIT 1
         )
        """,
        dict(ref=ref, pid=person_id),
    )
    return True
