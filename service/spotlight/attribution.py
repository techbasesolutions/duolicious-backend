"""Sign-up attribution from campaign links (spec 3.4).

A Spotlight post's CTA is a `/s/<key>` campaign link (see
`service.campaigns.make_campaign_link` / `record_click`). When someone
clicks it, browses, and eventually signs up, `attribute_signup` closes the
loop: it stamps the new person with the clicked link's key
(`person.spotlight_ref`) and marks the one specific click that led to the
sign-up (`campaign_click.signup_person_id`), so `post_stats` can report
clicks vs. signups per post.

What earns credit (F10): only the per-click receipt `record_click` minted
for that visitor. The `/s/<key>` key is shared by everyone who ever saw the
post, so it can never prove that a particular visitor clicked; crediting on
the key alone credited sign-ups no click earned and let one visitor's
sign-up claim another visitor's click. A receipt is minted per click, is
never handed to a bot, and is consumed the first time it is redeemed.

Deliberately conservative:
  - Never overwrites an existing `person.spotlight_ref` -- a member's
    attribution is fixed at their first sign-up, not their latest click.
    First touch wins, and in that case the receipt is left unconsumed, so
    it is not silently burned by a sign-up it could not credit.
  - A receipt earns credit at most once, and only when it is unexpired
    (7 days), not a bot's, and has never been consumed. All four of those
    facts are proven and the receipt consumed by the single atomic
    `_Q_CONSUME_RECEIPT` statement below.
  - Clicks recorded before migration 0048 carry a null receipt and are
    therefore never creditable: nothing proves who made them.
  - An unknown, empty, missing, or over-long ref is a silent no-op: this
    runs on every finish-onboarding call, most of which carry no ref at
    all, and a malformed value must never fail the signup itself.

Runs entirely inside the caller's transaction (the same one that graduates
the onboardee into a person row) -- it never opens one of its own.
"""
from __future__ import annotations

from typing import Optional

MAX_REF_LEN = 32

# The whole gate, in one statement. It proves in a single atomic UPDATE that
# the receipt exists, is unexpired, was not minted for a bot, and has never
# been consumed -- and consumes it in the same statement. Because the row is
# locked by this UPDATE and the `consumed_at IS NULL AND signup_person_id IS
# NULL` predicate is re-checked against the committed row after any
# concurrent writer commits, two concurrent sign-ups can never both claim
# one receipt: the loser's UPDATE matches nothing and returns no row.
#
# Nothing here picks a click by recency. `ORDER BY clicked_at DESC LIMIT 1`
# was the defect (F10) and must never come back: the newest unmatched click
# for a shared key belongs to whoever happened to click last, not to the
# person signing up.
_Q_CONSUME_RECEIPT = """
    UPDATE campaign_click
       SET signup_person_id = %(pid)s,
           consumed_at = NOW()
     WHERE receipt = %(ref)s
       AND signup_person_id IS NULL
       AND consumed_at IS NULL
       AND ua_class <> 'bot'
       AND clicked_at > NOW() - interval '7 days'
    RETURNING link_key
"""

_Q_STAMP_PERSON = """
    UPDATE person
       SET spotlight_ref = %(ref)s
     WHERE id = %(pid)s AND spotlight_ref IS NULL
    RETURNING id
"""

_Q_KNOWN_KEY = "SELECT 1 FROM campaign_link WHERE key = %(k)s"


def attribute_signup(tx, person_id: int, ref: Optional[str]) -> bool:
    if not ref or len(ref) > MAX_REF_LEN:
        return False

    # First touch wins: a person who already carries a ref is never
    # re-stamped, and their second receipt is deliberately left unconsumed
    # rather than burned by a sign-up it cannot credit.
    already = tx.execute(
        "SELECT spotlight_ref FROM person WHERE id = %(pid)s",
        dict(pid=person_id),
    ).fetchone()
    if already is None or already['spotlight_ref'] is not None:
        return False

    consumed = tx.execute(
        _Q_CONSUME_RECEIPT,
        dict(ref=ref, pid=person_id),
    ).fetchone()
    if consumed:
        # Stamped with the click's link_key, never with the receipt, so
        # every existing read of person.spotlight_ref keeps its meaning.
        stamped = tx.execute(
            _Q_STAMP_PERSON,
            dict(ref=consumed['link_key'], pid=person_id),
        ).fetchone()
        return bool(stamped)

    # Legacy compatibility window ONLY, and temporary. The web app and the
    # API deploy separately, so for one deploy window an old web build will
    # still send the shared campaign_link.key instead of a receipt. Such a
    # value stamps the person and credits no click. Wave 3b task 7
    # (documentation) is the removal trigger: delete this branch there, and
    # a bare key stops being accepted at all.
    known = tx.execute(_Q_KNOWN_KEY, dict(k=ref)).fetchone()
    if not known:
        return False

    stamped = tx.execute(_Q_STAMP_PERSON, dict(ref=ref, pid=person_id)).fetchone()
    return bool(stamped)
