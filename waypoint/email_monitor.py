"""Background IMAP IDLE monitor for incoming Garmin LiveTrack emails.

Watches an inbox for LiveTrack notification emails, extracts the session
link from the email body, and publishes it into the shared AppState -- the
web server (see waypoint.web) serves whatever AppState currently holds, so
there is no upload step anymore (FTP has been removed entirely).
"""

import email
import logging
import re
import time
from typing import Optional

import requests
from imapclient import IMAPClient

from .config import Config
from .link_probe import probe_iframe_embeddable
from .state import AppState

# Garmin's session/token format has varied slightly over time (case of the
# token, exact path shape), so the link is matched loosely: some non-slash,
# non-quote characters between "session/" and "/token/", followed by a hex
# token of either case.
LIVETRACK_LINK_RE = re.compile(
    r"(https?://livetrack\.garmin\.com/session/[^\s\"'<>]+/token/[A-Fa-f0-9]+)"
)


def extract_livetrack_link(raw_email: bytes) -> Optional[str]:
    """Extract a Garmin LiveTrack session link from a raw RFC 822 message.

    Garmin's notification template is multipart/related with only a
    text/html body -- there is no text/plain fallback -- so both part types
    are searched. Parts are decoded via get_payload(decode=True), which
    resolves quoted-printable/base64 transfer encodings (including soft
    line breaks that might otherwise split a URL across lines) before the
    link is matched.
    """
    msg = email.message_from_bytes(raw_email)
    body_parts = []

    if msg.is_multipart():
        parts = msg.walk()
    else:
        parts = [msg]

    for part in parts:
        if msg.is_multipart() and part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        body_parts.append(payload.decode(charset, errors="ignore"))

    body = "\n".join(body_parts)
    match = LIVETRACK_LINK_RE.search(body)
    return match.group(1) if match else None


class EmailMonitor:
    """Runs the IMAP IDLE loop, updating the shared AppState as emails arrive.

    Meant to run in its own background thread (see waypoint.__main__),
    independent of the web server.
    """

    def __init__(self, config: Config, state: AppState):
        self.config = config
        self.state = state
        self.log = logging.getLogger("waypoint.email")
        self.terminate = False
        self.max_retries = 3
        self.retry_delay = 30
        self.idle_timeout = 1200  # 20 minutes: renew IDLE before servers time it out
        self.server: Optional[IMAPClient] = None
        self._last_healthcheck: Optional[float] = None

    def stop(self) -> None:
        """Signal the run loop to exit at the next opportunity."""
        self.terminate = True

    # -- connection management -------------------------------------------------

    def connect(self) -> None:
        self.log.info(
            f"Connecting to IMAP server {self.config.imap_server}:{self.config.imap_port}..."
        )
        self.server = IMAPClient(
            self.config.imap_server, port=self.config.imap_port, ssl=self.config.imap_ssl
        )
        if self.config.imap_starttls and not self.config.imap_ssl:
            self.server.starttls()
        self.server.login(self.config.email_user, self.config.email_pass)
        self.server.select_folder("INBOX")
        self.state.record_check(connected=True, error=None)
        self.log.info("IMAP connection established successfully")

    def ensure_connection(self) -> bool:
        """Verify the connection is alive with NOOP, reconnecting if not."""
        try:
            self.server.noop()
            return True
        except Exception as e:
            self.log.warning(f"IMAP connection interrupted: {e}")
            return self.reconnect()

    def reconnect(self) -> bool:
        """Re-establish the IMAP connection with retries and backoff."""
        for attempt in range(self.max_retries):
            try:
                self.log.info(f"Connection attempt {attempt + 1}/{self.max_retries}...")
                try:
                    self.server.logout()
                except Exception:
                    pass
                self.connect()
                self.log.info("IMAP connection successfully restored")
                return True
            except Exception as e:
                self.log.warning(f"Connection attempt {attempt + 1} failed: {e}")
                self.state.record_check(connected=False, error=str(e))
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
        self.log.error("All connection attempts failed")
        self.state.record_check(connected=False, error="All reconnection attempts failed")
        return False

    def send_healthcheck(self) -> None:
        """Ping HEALTHCHECK_URL (rate-limited to once every 5 minutes).

        Only called from a clean IDLE tick or successful email processing --
        never after an error -- so an external monitor accurately reflects
        whether the service is actually healthy.
        """
        url = self.config.healthcheck_url
        if not url:
            return
        try:
            if self._last_healthcheck is None or time.time() - self._last_healthcheck > 300:
                requests.get(url=url, timeout=5)
                self._last_healthcheck = time.time()
        except Exception as e:
            self.log.warning(f"Error sending healthcheck: {e}")

    # -- email processing --------------------------------------------------------

    def process_new_emails(self) -> None:
        """Search for, extract links from, and trash new Garmin LiveTrack emails.

        Every branch always moves the processed UID to TRASH_FOLDER, even on
        error, so a broken message can never be reprocessed on every tick.
        """
        try:
            self.log.info("Searching for new Garmin LiveTrack emails...")
            try:
                messages = self.server.search(
                    [
                        "UNSEEN",
                        "OR",
                        "FROM",
                        "garmin.com",
                        "OR",
                        "FROM",
                        "connect.garmin.com",
                        "SUBJECT",
                        "LiveTrack",
                    ]
                )
            except Exception:
                self.log.warning("Advanced email search failed, using simple search")
                messages = self.server.search(["UNSEEN"])

            self.log.info(f"Found: {len(messages)} unread emails")
            if not messages:
                return

            for uid, data in self.server.fetch(messages, ["RFC822", "ENVELOPE"]).items():
                try:
                    envelope = data[b"ENVELOPE"]
                    sender = str(envelope.from_[0]) if envelope.from_ else ""
                    subject = str(envelope.subject) if envelope.subject else ""
                    self.log.info(f"Processing email {uid} from: {sender}, subject: {subject}")

                    link = extract_livetrack_link(data[b"RFC822"])

                    self.server.move(uid, self.config.trash_folder)
                    self.log.info(f"Email {uid} moved to trash")

                    if link:
                        self.log.info(f"Found Garmin LiveTrack link: {link}")
                        iframe_ok = probe_iframe_embeddable(link)
                        self.state.set_link(link, source="email", iframe_ok=iframe_ok)
                    else:
                        self.log.info("No Garmin LiveTrack link found in this email.")
                except Exception as e:
                    self.log.error(f"Error processing email {uid}: {e}")
                    try:
                        self.server.move(uid, self.config.trash_folder)
                        self.log.info(f"Email {uid} moved to trash despite error")
                    except Exception as move_error:
                        self.log.error(f"Error moving email {uid}: {move_error}")
        except Exception as e:
            self.log.error(f"Error retrieving emails: {e}")
            if not self.ensure_connection():
                raise

    # -- main loop -----------------------------------------------------------

    def run(self) -> None:
        """Connect, sweep for anything missed, then IDLE until told to stop."""
        try:
            self.connect()
        except Exception as e:
            self.log.error(f"Could not establish initial IMAP connection: {e}")
            self.state.record_check(connected=False, error=str(e))
            return

        try:
            # Pick up anything that arrived while the container was stopped.
            self.process_new_emails()
        except Exception as e:
            self.log.error(f"Initial inbox sweep failed: {e}")

        self.server.idle()
        self.log.info("IDLE session started.")
        idle_start_time = time.time()

        while not self.terminate:
            try:
                if time.time() - idle_start_time > self.idle_timeout:
                    self.log.info("Renewing IDLE session...")
                    self.server.idle_done()
                    self.server.idle()
                    idle_start_time = time.time()
                    self.log.info("IDLE session renewed")

                responses = self.server.idle_check(timeout=10)

                if responses:
                    self.log.info("New email notification received, processing emails...")
                    try:
                        self.server.idle_done()
                        self.process_new_emails()
                        self.log.info("Email processing completed, restarting IDLE...")
                        self.server.idle()
                        idle_start_time = time.time()
                        self.send_healthcheck()
                    except Exception as e:
                        self.log.error(f"Error during email processing: {e}")
                        if self.ensure_connection():
                            self.server.idle()
                            idle_start_time = time.time()
                        else:
                            break
                else:
                    self.send_healthcheck()
            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                self.log.error(f"Unexpected error in IDLE loop: {e}")
                time.sleep(5)
                if self.ensure_connection():
                    self.server.idle()
                    idle_start_time = time.time()
                else:
                    break

        try:
            self.server.idle_done()
        except Exception as e:
            self.log.error(f"Error stopping IDLE session: {e}")
        try:
            self.server.logout()
        except Exception as e:
            self.log.error(f"Error during IMAP logout: {e}")
        self.log.info("Garmin LiveTrack email monitor terminated")
