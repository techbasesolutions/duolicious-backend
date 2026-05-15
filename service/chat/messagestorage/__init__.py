from service.chat.messagestorage.inbox import (
        INBOX_CONTENT_ENCODING,
        UpsertConversationJob,
        process_upsert_conversation_batch,
)
from service.chat.messagestorage.mam import (
        process_store_mam_message_batch,
        StoreMamMessageJob)
from service.chat.messagestorage.setmessaged import (
        process_set_messaged_batch,
        SetMessagedJob)
from batcher import Batcher
from database import api_tx
from service.chat.message import AudioMessage, ChatMessage
from typing import Awaitable, Callable
from lxml import etree
from dataclasses import dataclass
import datetime
from service.chat.chatutil import (
    message_string_to_etree,
)


@dataclass(frozen=True)
class StoreMessageJob:
    store_mam_message_job: StoreMamMessageJob
    upsert_conversation_job: UpsertConversationJob
    messaged_job: SetMessagedJob


def store_message(
    from_username: str,
    to_username: str,
    from_id: int,
    to_id: int,
    msg_id: str,
    message: ChatMessage | AudioMessage,
    callback: Callable[[], None] | Callable[[], Awaitable[None]] | None = None
):
    timestamp = datetime.datetime.now().timestamp()

    content = etree.tostring(
        message_string_to_etree(
            message_body=message.body,
            to_username=to_username,
            from_username=from_username,
            id=msg_id,
        ),
        encoding='unicode',
        pretty_print=False,
    ).encode(INBOX_CONTENT_ENCODING)

    job = StoreMessageJob(
        store_mam_message_job=StoreMamMessageJob(
            timestamp_microseconds=int(timestamp * 1_000_000),
            from_username=from_username,
            to_username=to_username,
            id=msg_id,
            message_body=message.body,
            audio_uuid=(
                message.audio_uuid
                if isinstance(message, AudioMessage)
                else None
            ),
        ),
        upsert_conversation_job=UpsertConversationJob(
            from_username=from_username,
            to_username=to_username,
            msg_id=msg_id,
            content=content,
        ),
        messaged_job=SetMessagedJob(
            from_id=from_id,
            to_id=to_id,
        ),
    )

    _store_message_batcher.enqueue(job, callback)

    # Phase W push notifications — fire-and-forget message push.
    # Wrapped in a try/except so any push-stack failure (missing
    # VAPID keys, missing pywebpush, transient DB issue) can never
    # block the chat write path. send_to_user_safe is itself
    # fire-and-forget on a background thread.
    if from_id and to_id and from_id != to_id:
        try:
            _push_chat_message(
                from_id=from_id,
                to_id=to_id,
                body=message.body or '',
                is_audio=isinstance(message, AudioMessage),
            )
        except Exception:
            import traceback
            print('store_message: push trigger failed:')
            print(traceback.format_exc())


def _push_chat_message(from_id: int, to_id: int, body: str, is_audio: bool):
    """Look up sender display + UUID, fire web-push to recipient.
    Recipient sees: title=<sender name>, body=<message text>,
    tap → /chat/<sender uuid>. The `tag` collapses repeat messages
    in the same conversation so the OS notification tray doesn't
    stack 12 banners when a chatty user fires off a burst."""
    from service.notifications import send_to_user_safe, PUSH_ENABLED
    if not PUSH_ENABLED:
        return

    with api_tx('read committed') as tx:
        row = tx.execute(
            'SELECT name, uuid::text AS uuid FROM person WHERE id = %(id)s',
            dict(id=from_id),
        ).fetchone()
    if not row:
        return
    sender_name = row.get('name') or 'Someone'
    sender_uuid = row.get('uuid') or ''

    if is_audio:
        push_body = '(voice message)'
    else:
        # Cap at ~80 chars + ellipsis. Apple Push truncates around
        # 110 chars on the lock screen, but shorter reads cleaner.
        clean = body.replace('\n', ' ').strip()
        push_body = clean[:80] + ('…' if len(clean) > 80 else '')
        if not push_body:
            push_body = '(empty message)'

    send_to_user_safe(
        person_id=to_id,
        title=sender_name,
        body=push_body,
        url=f'/chat/{sender_uuid}',
        tag=f'chat:{sender_uuid}',
        event_kind="message",
    )


def _process_store_message_batch(batch: list[StoreMessageJob]):
    store_mam_message_jobs = [
            job.store_mam_message_job
            for job in batch]

    upsert_conversation_jobs = [
            job.upsert_conversation_job
            for job in batch]

    messaged_jobs = [
            job.messaged_job
            for job in batch]

    with api_tx('read committed') as tx:
        process_store_mam_message_batch(tx, store_mam_message_jobs)
        process_upsert_conversation_batch(tx, upsert_conversation_jobs)
        process_set_messaged_batch(tx, messaged_jobs)


_store_message_batcher = Batcher[StoreMessageJob](
    process_fn=_process_store_message_batch,
    flush_interval=0.5,
    min_batch_size=1,
    max_batch_size=1000,
    retry=False,
)


_store_message_batcher.start()
