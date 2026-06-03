"""Thread‑safe SMTP helper with typed API and automatic retries.

Phase W: gained an opt-in HTTPS path to Resend's API at /emails. Set
`DUO_USE_RESEND_API=true` to bypass smtplib entirely and POST messages
over port 443 instead of 25/465/587. DigitalOcean droplets block
outbound SMTP ports by default (anti-spam policy), so the HTTPS path
is the only practical way to send mail from a DO-hosted backend.
When the flag is on, `host`/`port`/`username` are ignored; `password`
is used as the Resend API key (matches the `DUO_SMTP_PASS=re_...`
configuration pattern Resend itself documents for SMTP-bridge clients).
"""

import os
import smtplib
import threading
import time
import traceback
from contextlib import suppress
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from service.config import EMAIL_DOMAIN, PRODUCT_NAME

SMTP_HOST: str = os.environ["DUO_SMTP_HOST"]
SMTP_PORT: int = int(os.environ["DUO_SMTP_PORT"])
SMTP_USER: str = os.environ["DUO_SMTP_USER"]
SMTP_PASS: str = os.environ["DUO_SMTP_PASS"]
USE_RESEND_API: bool = os.environ.get("DUO_USE_RESEND_API", "false").lower() in (
    "true",
    "1",
    "yes",
)
RESEND_API_URL: str = "https://api.resend.com/emails"

# Phase W staging: ahavah.app isn't verified in Resend yet (domain not
# even registered). Override every outbound from-address to the universal
# `onboarding@resend.dev` placeholder (works on every Resend account
# without domain verification). Unset to restore caller-supplied from.
RESEND_FROM_OVERRIDE: str = os.environ.get("DUO_RESEND_FROM_OVERRIDE", "")

# Production safety: if RESEND_FROM_OVERRIDE leaks into prod past launch,
# every welcome / OTP / launch mail goes from a non-branded address and
# silently tanks the ahavah.app sender reputation. Log a loud warning at
# import time so it's visible in container boot logs (audit Email LOW).
if RESEND_FROM_OVERRIDE and os.environ.get("DUO_ENV") == "prod":
    print(
        f"WARNING: DUO_RESEND_FROM_OVERRIDE is set ({RESEND_FROM_OVERRIDE!r}) "
        f"in production — every outbound From will be rewritten. Unset this "
        f"env var in .env.production before public launch."
    )


class Smtp:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
    ) -> None:
        self.host: str = host
        self.port: int = port
        self.username: str = username
        self.password: str = password
        self._smtp: smtplib.SMTP | None = None

        self._lock: threading.RLock = threading.RLock()

        # Phase W: defer SMTP connection until first send. Eager-connecting at
        # __init__ time crashed module imports when SMTP was unreachable
        # (e.g. DigitalOcean blocks outbound 587 → smtplib hangs → Smtp.__init__
        # raises → smtp module import fails → entire api container fails to
        # boot). _try_send already calls _connect lazily when self._smtp is None.

    def _connect(self) -> None:
        """(Re)‑establish an SMTP connection (protected by *lock*)."""
        with self._lock:
            if self._smtp is not None:
                try:
                    self._smtp.noop()
                    return  # connection still healthy
                except smtplib.SMTPServerDisconnected:
                    self._smtp = None

            try:
                print(f"Establishing connection to SMTP server at {self.host}")
                smtp = smtplib.SMTP(self.host, self.port, timeout=30)
                smtp.ehlo()

                if smtp.has_extn("starttls"):
                    smtp.starttls()
                    smtp.ehlo()  # re-identify as TLS is now in effect
                    print("STARTTLS supported and initiated.")
                else:
                    print("STARTTLS not supported by server.")

                smtp.login(self.username, self.password)
                self._smtp = smtp
                print(f"Connection to SMTP server at {self.host} established")
            except Exception as exc:
                print(f"Failed to connect to SMTP server: {exc}")
                self._smtp = None
                raise

    def _try_send(
        self,
        *,
        subject: str,
        body: str,
        to_addr: str,
        from_addr: str | None = None,
        reply_to: str | None = None,
        list_unsubscribe: str | None = None,
    ) -> None:
        # Phase W: branch to Resend HTTPS API when DUO_USE_RESEND_API=true.
        # See module docstring for context (DO blocks outbound SMTP ports).
        if USE_RESEND_API:
            self._try_send_resend_api(
                subject=subject,
                body=body,
                to_addr=to_addr,
                from_addr=from_addr,
                reply_to=reply_to,
                list_unsubscribe=list_unsubscribe,
            )
            return

        if self._smtp is None:
            # Lazily reconnect if previous attempt failed.
            self._connect()

        if self._smtp is None:
            raise Exception("Connection couldn't be established")

        _from_addr: str = from_addr or f"no-reply@{EMAIL_DOMAIN}"

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{PRODUCT_NAME} <{_from_addr}>"
        msg["To"] = to_addr
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        if list_unsubscribe:
            # RFC 8058 one-click unsubscribe; required by Gmail/Yahoo for
            # bulk senders since Feb 2024 (audit Email MED).
            msg["List-Unsubscribe"] = list_unsubscribe
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.attach(MIMEText(body, "html"))

        self._smtp.sendmail(
            from_addr=_from_addr,
            to_addrs=[to_addr],
            msg=msg.as_string(),
        )

    def _try_send_resend_api(
        self,
        *,
        subject: str,
        body: str,
        to_addr: str,
        from_addr: str | None = None,
        reply_to: str | None = None,
        list_unsubscribe: str | None = None,
    ) -> None:
        """Send via Resend's HTTPS API (port 443) instead of SMTP.

        Uses `requests` (already installed transitively via boto3) instead
        of `urllib` because Cloudflare in front of Resend returns "1010"
        403 against urllib's TLS fingerprint. `requests` has a fingerprint
        Cloudflare accepts as legitimate.

        self.password = Resend API key (matches the SMTP-bridge config
        pattern where DUO_SMTP_PASS holds re_…).
        """
        import requests

        # Phase W staging: force every from-address to RESEND_FROM_OVERRIDE
        # if set, because ahavah.app isn't a verified Resend domain yet.
        # Unset env var = use caller-supplied from_addr as normal.
        _from_addr: str = (
            RESEND_FROM_OVERRIDE or from_addr or f"no-reply@{EMAIL_DOMAIN}"
        )
        payload: dict = {
            "from": f"{PRODUCT_NAME} <{_from_addr}>",
            "to": [to_addr],
            "subject": subject,
            "html": body,
        }
        if reply_to:
            payload["reply_to"] = reply_to
        if list_unsubscribe:
            payload["headers"] = {
                "List-Unsubscribe": list_unsubscribe,
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        resp = requests.post(
            RESEND_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {self.password}",
                "User-Agent": "ahavah-backend/1.0",
            },
            timeout=15,
        )
        if resp.status_code >= 300:
            raise Exception(
                f"Resend API HTTP {resp.status_code}: {resp.text[:500]}"
            )

    def send(
        self,
        *,
        subject: str,
        body: str,
        to_addr: str,
        from_addr: str | None = None,
        reply_to: str | None = None,
        list_unsubscribe: str | None = None,
        retries: int | None = None,
        backoff: int | None = None,
    ) -> None:
        """Send an email, retrying on failure.

        Back‑off doubles on every failed attempt: *backoff* × 2^(n - 1).

        Optional headers:
          reply_to:         set Reply-To: so replies route to a human
                            inbox (e.g. user feedback → user's email).
          list_unsubscribe: RFC 8058 one-click unsubscribe — required by
                            Gmail/Yahoo for bulk senders. Format:
                            "<mailto:unsub@...>, <https://...?token=...>"
        """
        max_attempts: int = 1 + (2 if retries is None else retries)

        for attempt in range(1, max_attempts + 1):
            try:
                with self._lock:
                    self._try_send(
                        subject=subject,
                        body=body,
                        to_addr=to_addr,
                        from_addr=from_addr,
                        reply_to=reply_to,
                        list_unsubscribe=list_unsubscribe,
                    )
                return  # Success
            except Exception:
                print(traceback.format_exc())
                if attempt == max_attempts:
                    print("All retry attempts exhausted. Giving up.")

                delay_base: float = 1.0 if backoff is None else backoff
                delay = delay_base * (2 ** (attempt - 1))
                print(f"Attempt {attempt} failed; retrying in {delay:.1f}s.")
                time.sleep(delay)

                # Best effort reconnect for the next iteration. Skip when
                # on the HTTPS Resend path: _connect() opens an SMTP socket
                # on port 587 which DigitalOcean droplets block by default,
                # causing each "reconnect" to hang for the smtplib timeout
                # (30s) and turning a 7s backoff into 90s+ of waiting.
                if not USE_RESEND_API:
                    with suppress(Exception):
                        self._connect()

    # ------------------------------------------------------------------

    def quit(self) -> None:
        """Explicitly close the SMTP connection."""
        with self._lock:
            if self._smtp is not None:
                try:
                    self._smtp.quit()
                except Exception as exc:
                    print(f"Error while quitting SMTP connection: {exc}")
                finally:
                    self._smtp = None

    def __del__(self) -> None:
        with suppress(Exception):  # _smtp may already be closed
            self.quit()


def make_aws_smtp() -> Smtp:
    return Smtp(SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS)


aws_smtp: Smtp = make_aws_smtp()
