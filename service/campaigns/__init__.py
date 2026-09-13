"""Campaign email send log, per-member frequency cap, and click links.

Spec 3.5: at most one campaign email per member per 7 days (E4/E5 are
member-triggered and exempt); each send carries a campaign_id so a
retried request cannot send twice. Spec 3.4: every CTA is a /s/<key>
link that counts clicks."""
from __future__ import annotations

import secrets
from typing import Optional

from service.config import WEB_BASE_URL

_Q_SAME_RUN = """
    SELECT 1 FROM email_send_log
     WHERE person_id = %(pid)s AND campaign = %(c)s AND campaign_id = %(cid)s
"""
_Q_RECENT = """
    SELECT 1 FROM email_send_log
     WHERE person_id = %(pid)s AND sent_at > NOW() - make_interval(days => %(d)s)
     LIMIT 1
"""

def can_send(tx, person_id: int, campaign: str, campaign_id: str, *,
             cap_days: int = 7, exempt: bool = False) -> bool:
    if tx.execute(_Q_SAME_RUN, dict(pid=person_id, c=campaign, cid=campaign_id)).fetchone():
        return False
    if exempt:
        return True
    return tx.execute(_Q_RECENT, dict(pid=person_id, d=cap_days)).fetchone() is None

# Footer unsubscribes are per-scope, and each scope is stamped on a
# different row (see service.unsubscribe._Q_UNSUB). A campaign must honour
# its OWN scope's stamp before the next run, otherwise a member who clicked
# "unsubscribe" in the footer still gets the following week's email.
_Q_UNSUBSCRIBED = {
    # `notifications` turns every email_* channel off in one upsert, so the
    # scope is "unsubscribed" exactly when all five are FALSE. Anything less
    # is a member who tuned individual toggles, not one who opted out.
    'notifications': """
        SELECT 1 FROM notification_preference
         WHERE person_id = %(pid)s
           AND NOT email_messages
           AND NOT email_matches
           AND NOT email_likes
           AND NOT email_verification
           AND NOT email_profile_views
    """,
    'community': """
        SELECT 1 FROM person
         WHERE id = %(pid)s AND community_unsubscribed_at IS NOT NULL
    """,
}

def campaign_unsubscribed(tx, person_id: int, scope: str) -> bool:
    """True iff this member has unsubscribed from `scope`. Unknown scopes
    (and the scopes that have no member-facing campaign, e.g. `waitlist`)
    are never treated as unsubscribed."""
    q = _Q_UNSUBSCRIBED.get(scope)
    if q is None or not person_id:
        return False
    return tx.execute(q, dict(pid=person_id)).fetchone() is not None

def log_send(tx, person_id: int, campaign: str, campaign_id: str,
             message_id: Optional[str]) -> None:
    tx.execute(
        """
        INSERT INTO email_send_log (person_id, campaign, campaign_id, message_id)
        VALUES (%(pid)s, %(c)s, %(cid)s, %(mid)s)
        ON CONFLICT (person_id, campaign, campaign_id) DO NOTHING
        """,
        dict(pid=person_id, c=campaign, cid=campaign_id, mid=message_id))

def make_campaign_link(tx, kind: str, target_url: str,
                       subject_person_id: Optional[int] = None) -> str:
    """Mint a /s/<key> link. `target_url` must stay on our own web app:
    /s/<key> redirects to whatever is stored here, so accepting a foreign
    target would turn every campaign email into an open redirect."""
    base = WEB_BASE_URL.rstrip('/')
    if not str(target_url).startswith(base):
        raise ValueError(f"campaign link target must start with {base}")
    key = secrets.token_urlsafe(6)
    tx.execute(
        """
        INSERT INTO campaign_link (key, kind, target_url, subject_person_id)
        VALUES (%(k)s, %(kind)s, %(url)s, %(pid)s)
        """,
        dict(k=key, kind=kind, url=target_url, pid=subject_person_id))
    return f"{WEB_BASE_URL.rstrip('/')}/s/{key}"

def _ua_class(ua: str) -> str:
    u = (ua or '').lower()
    if 'facebookexternalhit' in u or 'bot' in u or 'crawler' in u:
        return 'bot'
    if 'mobile' in u or 'android' in u or 'iphone' in u:
        return 'mobile'
    return 'desktop' if u else 'unknown'

def record_click(tx, key: str, user_agent: str) -> Optional[str]:
    row = tx.execute("SELECT target_url FROM campaign_link WHERE key = %(k)s",
                     dict(k=key)).fetchone()
    if not row:
        return None
    tx.execute("INSERT INTO campaign_click (link_key, ua_class) VALUES (%(k)s, %(u)s)",
               dict(k=key, u=_ua_class(user_agent)))
    return row['target_url']
