import unittest
from unittest.mock import patch, AsyncMock
from service.cron.notifications import (
    PersonNotification,
    send_notification,
)
import asyncio


def _row(*, has_live_push, push_messages):
    return PersonNotification(
        person_uuid='2',
        last_intro_notification_seconds=1693786048,
        last_chat_notification_seconds=1693786048,
        has_intro=True,
        has_chat=True,
        last_intro_seconds=1693786124,
        last_chat_seconds=100,
        name='jk',
        email='user.1@gmail.com',
        chats_drift_seconds=0,
        intros_drift_seconds=86400,
        has_live_push=has_live_push,
        push_messages=push_messages,
    )


class TestSendNotification(unittest.TestCase):
    """The cron is the EMAIL FALLBACK: real-time web push covers reachable
    users (service/chat/messagestorage), so the cron only emails users push
    can't reach."""

    @patch('service.cron.notifications.send_email_notification',
           new_callable=AsyncMock)
    def test_skips_email_when_push_reachable(self, mock_send_email):
        asyncio.run(send_notification(
            _row(has_live_push=True, push_messages=True)))
        mock_send_email.assert_not_called()

    @patch('service.cron.notifications.send_email_notification',
           new_callable=AsyncMock)
    def test_emails_when_no_subscription(self, mock_send_email):
        asyncio.run(send_notification(
            _row(has_live_push=False, push_messages=True)))
        mock_send_email.assert_awaited_once()

    @patch('service.cron.notifications.send_email_notification',
           new_callable=AsyncMock)
    def test_emails_when_message_push_disabled(self, mock_send_email):
        asyncio.run(send_notification(
            _row(has_live_push=True, push_messages=False)))
        mock_send_email.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
