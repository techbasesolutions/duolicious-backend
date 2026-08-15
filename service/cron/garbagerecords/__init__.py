from database.asyncdatabase import api_tx
from service.cron.garbagerecords.sql import *
from service.cron.cronutil import print_stacktrace, MAX_RANDOM_START_DELAY
from emails.waitlist_admin import FROM_ADDR, TO_ADDR
from smtp import aws_smtp
import asyncio
import html
import os
import random

GARBAGE_RECORDS_POLL_SECONDS = int(os.environ.get(
    'DUO_CRON_GARBAGE_RECORDS_POLL_SECONDS',
    str(10), # 10 seconds
))

print(f'Hello from cron module: {__name__}')

def _nsfw_removed_row_html(r: dict) -> str:
    name = html.escape(str(r.get('person_name') or 'Unknown'))
    uuid = html.escape(str(r.get('uuid') or ''))
    score = r.get('score')
    score_text = html.escape(f'{score:.3f}') if score is not None else ''
    return (
        f"<tr>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #ddd;'>{name}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #ddd;font-family:monospace;'>{uuid}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #ddd;'>{score_text}</td>"
        f"</tr>"
    )

def _nsfw_admin_notice_html(removed: list[dict]) -> str:
    """F19: plain internal-ops HTML listing each NSFW auto-removed photo
    (person name, photo uuid, score) so a false positive on a 34-member
    faith community is visible to the operator the hour it happens."""
    rows_html = "".join(_nsfw_removed_row_html(r) for r in removed)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"/></head>
<body style="font-family:Arial,Helvetica,sans-serif;color:#0F0B1F;">
<p>The NSFW auto-removal sweep just hard-deleted {len(removed)} photo(s) for
having an nsfw_score above 0.8. The photos were staged into undeleted_photo
so the CDN cleaner removes them, but if any of these are false positives the
affected member's profile picture just vanished without warning, so please
review this list.</p>
<table style="border-collapse:collapse;">
<tr>
<th style="padding:6px 12px;text-align:left;border-bottom:2px solid #333;">Person</th>
<th style="padding:6px 12px;text-align:left;border-bottom:2px solid #333;">Photo UUID</th>
<th style="padding:6px 12px;text-align:left;border-bottom:2px solid #333;">NSFW score</th>
</tr>
{rows_html}
</table>
</body>
</html>"""

def _send_nsfw_admin_notice(removed: list[dict]) -> None:
    aws_smtp.send(
        subject=f"NSFW auto-removal: {len(removed)} photo(s)",
        body=_nsfw_admin_notice_html(removed),
        to_addr=TO_ADDR,
        from_addr=FROM_ADDR,
    )

async def delete_garbage_records_once():
    async with api_tx() as tx:
        cur = await tx.execute(Q_DELETE_GARBAGE_RECORDS)
        rows = await cur.fetchall()

    try:
        count = rows[0]['count']
    except:
        count = 0

    if count:
        print(f'Deleted {count} garbage record(s)')

    # F19: notify the admin inbox of every NSFW auto-removal so a false
    # positive is seen the hour it happens, not discovered by the member.
    try:
        nsfw_removed = (rows[0].get('nsfw_removed') or []) if rows else []
    except:
        nsfw_removed = []
    if nsfw_removed:
        _send_nsfw_admin_notice(nsfw_removed)

async def delete_garbage_records_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(delete_garbage_records_once)
        await asyncio.sleep(GARBAGE_RECORDS_POLL_SECONDS)
