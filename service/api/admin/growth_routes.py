from __future__ import annotations

import re

import duotypes as t
from flask import abort, request

from database import api_tx
from service.admin import require_admin, record_audit
from service.api.decorators import aget, apost
from service.growth.queries import growth_stats
import emails.send_spotlight_announcement as e1
import emails.send_community_weekly as e2
import emails.send_reinvite as e3

@aget('/admin/growth/stats')
def get_admin_growth_stats(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        return growth_stats(tx)

_CAMPAIGNS = {'e1': e1, 'e2': e2, 'e3': e3}

# Deliberately simple: this only has to reject things that are obviously not
# an address before we hand one to SMTP. Real validation is the mail server's
# job, and a preview is admin-only.
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

_Q_LAST_SENT = """
    SELECT campaign, max(sent_at) AS at,
           (array_agg(campaign_id ORDER BY sent_at DESC))[1] AS cid,
           count(*) AS n
      FROM email_send_log GROUP BY campaign
"""

@aget('/admin/growth/emails')
def get_admin_growth_emails(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        last_sent = {r['campaign']: r for r in tx.execute(_Q_LAST_SENT).fetchall()}
    # recipient_count() opens its own transaction per campaign, so it must be
    # called OUTSIDE the block above: the api connection lock is not
    # reentrant and nesting would deadlock the request.
    out = []
    for key, mod in _CAMPAIGNS.items():
        last = last_sent.get(key)
        out.append(dict(campaign=key, recipients=mod.recipient_count(),
                        last_sent_at=last['at'].isoformat() if last and last['at'] else None,
                        last_campaign_id=last['cid'] if last else None, system=False))
    # e4 (the member invite) and e5 (the card-went-live receipt) are sent by
    # the platform itself, not run from this admin screen: they have no
    # recipients()/recipient_count() module to ask, so their count comes
    # straight from the email_send_log rows the query above already grouped,
    # and they carry system=True so the Growth tab can tell them apart from
    # the three admin-run campaigns above.
    for key in ('e4', 'e5'):
        last = last_sent.get(key)
        out.append(dict(campaign=key, recipients=int(last['n']) if last else 0,
                        last_sent_at=last['at'].isoformat() if last and last['at'] else None,
                        last_campaign_id=last['cid'] if last else None, system=True))
    return dict(campaigns=out)

@apost('/admin/growth/emails/<campaign>/preview')
def post_admin_growth_email_preview(s: t.SessionInfo, campaign: str):
    require_admin(s)
    mod = _CAMPAIGNS.get(campaign) or abort(404)
    to = (request.get_json(silent=True) or {}).get('to') or abort(400)
    if not isinstance(to, str) or not _EMAIL_RE.match(to):
        abort(400)
    preview_row = getattr(mod, 'preview_row', None)
    row = preview_row(to) if preview_row else dict(person_id=0, email=to, name='Preview')
    subject, html = mod.build_for(row)
    from smtp import make_aws_smtp
    make_aws_smtp().send(subject=subject, body=html, to_addr=to, from_addr=mod.FROM_ADDR)
    return dict(ok=True)

@apost('/admin/growth/emails/<campaign>/send')
def post_admin_growth_email_send(s: t.SessionInfo, campaign: str):
    """Queue a run and return immediately (F07). Nothing here talks to SMTP
    any more: the response reports how many messages were QUEUED, and the
    `emailoutbox` cron sends them. Progress is read back from
    `/admin/growth/emails/<campaign>/status/<campaign_id>`."""
    require_admin(s)
    mod = _CAMPAIGNS.get(campaign) or abort(404)
    body = request.get_json(silent=True) or {}
    cid = body.get('campaign_id') or abort(400)
    dry = bool(body.get('dry_run', True))
    from service.campaigns.runner import run_campaign
    from service.unsubscribe import make_url as _unsub_url
    from service.config import WEB_BASE_URL
    res = run_campaign(api_tx, campaign, cid, mod.recipients(), mod.build_for, send=not dry,
                       from_addr=mod.FROM_ADDR,
                       unsub_scope=mod.UNSUB_SCOPE,
                       cap_days=getattr(mod, 'CAP_DAYS', 7),
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(mod.UNSUB_SCOPE, e, WEB_BASE_URL)}>",
                       post_send=getattr(mod, 'post_send', None))
    # The audit row is written AFTER the run, not inside it, on purpose: a run
    # spans one transaction per recipient (see service/campaigns/runner.py), so
    # there is no single transaction the audit could share. `res` already
    # carries campaign_id and dry_run, so only `campaign` is added here.
    with api_tx() as tx:
        record_audit(tx, s, 'growth.email.send', metadata=dict(campaign=campaign, **res))
    return res


@aget('/admin/growth/emails/<campaign>/status/<campaign_id>')
def get_admin_growth_email_status(s: t.SessionInfo, campaign: str, campaign_id: str):
    """Where a queued run actually got to. `acceptance_unknown` is the one
    that needs a human: those messages were reserved by a drain that never
    reported back, so whether they reached the member is genuinely unknown
    and the outbox refuses to guess (F08)."""
    require_admin(s)
    _CAMPAIGNS.get(campaign) or abort(404)
    from service.campaigns import outbox
    with api_tx('read committed') as tx:
        return outbox.status(tx, campaign, campaign_id)
