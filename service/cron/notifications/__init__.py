from database.asyncdatabase import api_tx
from dataclasses import dataclass
from service.cron.notifications.sql import (
    Q_UNREAD_INBOX,
)
from service.cron.notifications.template import (
    big_part,
    frequency_url,
)
from emails.notification import new_message_email
from service.config import WEB_BASE_URL
from service.cron.cronutil import (
    MAX_RANDOM_START_DELAY,
    print_stacktrace,
)
from commonsql import (
    Q_UPSERT_LAST_INTRO_NOTIFICATION_TIME,
    Q_UPSERT_LAST_CHAT_NOTIFICATION_TIME,
)
import asyncio
from smtp import make_aws_smtp
import os
import random
import json
import traceback

EMAIL_POLL_SECONDS = int(os.environ.get(
    'DUO_CRON_EMAIL_POLL_SECONDS',
    str(10), # 10 seconds
))

print(f'Hello from cron module: {__name__}')

@dataclass
class PersonNotification:
    person_uuid: int
    last_intro_notification_seconds: int
    last_chat_notification_seconds: int
    last_intro_seconds: int
    last_chat_seconds: int
    has_intro: bool
    has_chat: bool
    name: str
    email: str
    chats_drift_seconds: int
    intros_drift_seconds: int
    has_live_push: bool
    push_messages: bool
    email_messages: bool

def do_send_notification(row: PersonNotification):
    email = row.email
    has_intro = row.has_intro
    has_chat = row.has_chat
    intros_drift_seconds = row.intros_drift_seconds
    chats_drift_seconds = row.chats_drift_seconds
    last_intro_notification_seconds = row.last_intro_notification_seconds
    last_chat_notification_seconds = row.last_chat_notification_seconds
    last_intro_seconds = row.last_intro_seconds
    last_chat_seconds = row.last_chat_seconds

    is_intro_sendable = (
        has_intro and
        intros_drift_seconds >= 0 and
        last_intro_notification_seconds + intros_drift_seconds < last_intro_seconds
    )

    is_chat_sendable = (
        has_chat and
        chats_drift_seconds >= 0 and
        last_chat_notification_seconds + chats_drift_seconds < last_chat_seconds
    )

    return (is_intro_sendable or is_chat_sendable)

def do_send_email_notification(row: PersonNotification):
    is_example = row.email.lower().endswith('@example.com')

    return do_send_notification(row) and not is_example

async def send_email_notification(row: PersonNotification):
    if not do_send_email_notification(row):
        print('Email notification suppressed (example.com):',
              f'person_uuid={row.person_uuid}')
        return

    # Setting message email frequency to "Never" is the one-click opt-out;
    # also doubles as the RFC-8058 List-Unsubscribe target.
    unsubscribe_url = frequency_url(row.email, 'Every', 'Never')
    send_args = dict(
        subject="You have a new message on Ahavah",
        body=new_message_email(
            headline=big_part(row.has_intro, row.has_chat),
            open_url=f"{WEB_BASE_URL}/inbox",
            unsubscribe_url=unsubscribe_url,
        ),
        to_addr=row.email,
        list_unsubscribe=f"<{unsubscribe_url}>",
    )

    aws_smtp = make_aws_smtp()
    await asyncio.to_thread(aws_smtp.send, **send_args)

async def send_notification(row: PersonNotification):
    # The real-time web push fires in service/chat/messagestorage. This cron
    # is the EMAIL FALLBACK for members who haven't seen the message after
    # 10+ minutes offline. Log only non-sensitive fields (the full row
    # carries email — a bearer-equivalent — keep it out of logs).
    #
    # 2026-07-29: a live push_subscription row no longer skips the email.
    # Web push is fire-and-forget — a stale endpoint looks identical to a
    # delivered one — and the caller bumps the notification watermark
    # either way, so a skip here silently ate the only notification the
    # member would ever get (Abby never learned of 2 unread messages;
    # Laura of 1; both had watermarks burned minutes after the message).
    # If the member is still offline with unread messages when this cron
    # fires, the push evidently didn't bring them back — email them.
    sketch = f"person_uuid={row.person_uuid} intro={row.has_intro} chat={row.has_chat}"
    if not row.email_messages:
        print('Message email disabled by user; skipping:', sketch, flush=True)
        return
    print('Sending email notification:', sketch, flush=True)
    await send_email_notification(row)

async def update_last_notification_time(row: PersonNotification):
    params = dict(username=row.person_uuid)

    async with api_tx('read committed') as tx:
        if row.has_intro:
            await tx.execute(Q_UPSERT_LAST_INTRO_NOTIFICATION_TIME, params)
        if row.has_chat:
            await tx.execute(Q_UPSERT_LAST_CHAT_NOTIFICATION_TIME, params)

async def maybe_send_notification(row: PersonNotification):
    if not do_send_notification(row):
        return

    await send_notification(row)
    await update_last_notification_time(row)

async def send_notifications_once():
    async with api_tx('read committed') as tx:
        await tx.execute('SET LOCAL statement_timeout = 15000') # 15 seconds
        cur = await tx.execute(Q_UNREAD_INBOX)
        rows = await cur.fetchall()

    person_notifications = [PersonNotification(**j) for j in rows]

    for row in person_notifications:
        await maybe_send_notification(row)

async def send_notifications_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(send_notifications_once)
        await asyncio.sleep(EMAIL_POLL_SECONDS)
