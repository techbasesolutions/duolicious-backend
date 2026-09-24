from service.cron.autodeactivate2 import autodeactivate2_forever
from service.cron.betareengagement import send_beta_reengagement_forever
from service.cron.checkphotos import check_photos_forever
from service.cron.entitlements import entitlements_forever
from service.cron.garbagerecords import delete_garbage_records_forever
from service.cron.notifications import send_notifications_forever
from service.cron.nsfwphotorunner import predict_nsfw_photos_forever
from service.cron.pendingdeletion import hard_delete_expired_forever
from service.cron.photocleaner import clean_photos_forever
from service.cron.audiocleaner import clean_audio_forever
from service.cron.verificationjobrunner import verify_forever
from service.cron.profilereporter import report_profiles_forever
from service.cron.fireholbuilder import build_firehol_forever
from service.cron.spotlightretention import spotlight_retention_forever
from service.cron.spotlightcleanup import spotlight_cleanup_forever
from service.cron.emailoutbox import email_outbox_forever
from service.cron.communityweekly import community_weekly_forever
import asyncio
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer
from database.asyncdatabase import check_connections_forever

class HealthCheckHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'Healthy')
        else:
            self.send_response(404)
            self.end_headers()

async def http_server():
    with TCPServer(('0.0.0.0', 8080), HealthCheckHandler) as httpd:
        print("Serving health check on port 8080...", flush=True)
        await asyncio.to_thread(httpd.serve_forever)

async def main():
    await asyncio.gather(
        # Fetched: 11k, returned: 670k <- unoptimized
        # Fetched:  1k, returned:  84k <- optimized
        autodeactivate2_forever(),

        # Fetched: 0.1k, returned: 2k
        delete_garbage_records_forever(),

        # Fetched: 0.1k, returned: 100k
        clean_photos_forever(),

        clean_audio_forever(),

        predict_nsfw_photos_forever(),

        # Should only be enabled when it's likely that the object store contains
        # photos which aren't tracked by the DB
        # check_photos_forever(),

        # Fetched: 9k, returned: 70k
        send_notifications_forever(),

        verify_forever(),

        report_profiles_forever(),

        # Phase W: hard-delete pending-deletion person rows past the
        # 7-day grace window. Runs hourly.
        hard_delete_expired_forever(),

        # Re-engagement: beta testers who joined >=7 days ago but still
        # have no waitlist demographics — send the follow-up once. Runs
        # every 6 hours; gated by beta_signup.reengagement_sent_at.
        send_beta_reengagement_forever(),

        # Single writer of the FireHOL binary blocklist the api workers
        # mmap (antiabuse/firehol). Every 4 hours.
        build_firehol_forever(),

        # F10: strips 'premium' from person.entitlements once
        # subscription_expires_at passes. Was dead code (expire_stale had
        # zero callers) until this task. Runs hourly.
        entitlements_forever(),

        # Community Spotlight: enqueues a cleanup job for the artwork of
        # published rows older than RETENTION_DAYS (90). Clears nothing
        # itself. Runs once a day.
        spotlight_retention_forever(),

        # The single drain of the spotlight cleanup job table: the only
        # place a card image is deleted from the object store, and the only
        # place a row's image_key is cleared. Every 10 minutes.
        spotlight_cleanup_forever(),

        # The single drain of the durable email outbox: the only place SMTP
        # is spoken anywhere in the system. Every 30 seconds.
        email_outbox_forever(),

        # e2, the weekly community email. Monday 12:00 UTC, guarded by the
        # week's campaign id so a repeat tick, a restart and an operator
        # pressing Send in the same week all collide on one send. Off
        # unless DUO_CRON_COMMUNITY_WEEKLY_ENABLED=1.
        community_weekly_forever(),

        check_connections_forever(),

        http_server(),
    )

if __name__ == '__main__':
    asyncio.run(main())
