"""Entry point: `python -m waypoint`.

Starts the background IMAP monitor thread and serves the Flask app (via
waitress) in the foreground. SIGINT/SIGTERM stop the monitor cleanly before
the process exits.
"""

import logging
import signal
import sys
import threading

from dotenv import load_dotenv
from waitress import create_server

from .config import Config
from .email_monitor import EmailMonitor
from .state import AppState
from .web import create_app


def main() -> None:
    load_dotenv()
    config = Config.from_env()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("waypoint")

    try:
        config.validate_credentials()
    except ValueError as e:
        log.error(str(e))
        log.error("Please create a .env file based on .env.example")
        sys.exit(1)

    if config.admin_enabled:
        log.info("Admin interface enabled at /admin")
    else:
        log.info("Admin interface disabled (set ADMIN_USER and ADMIN_PASSWORD to enable)")

    state = AppState(state_dir=config.state_dir)
    state.load()

    monitor = EmailMonitor(config, state)

    app = create_app(config, state)
    # create_server (rather than the serve() shortcut) gives us a handle to
    # close from the signal handler -- serve() blocks forever and installs
    # no signal handling of its own, so without this, SIGTERM would just get
    # silently overridden by handle_signal below and the container would
    # sit until Docker's SIGKILL, skipping the monitor's clean IMAP logout.
    server = create_server(app, host="0.0.0.0", port=config.web_port)
    shutting_down = threading.Event()

    def handle_signal(signum, frame):
        log.info("Shutting down Garmin LiveTrack...")
        monitor.stop()
        shutting_down.set()
        server.close()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    monitor_thread = threading.Thread(target=monitor.run, name="email-monitor", daemon=True)
    monitor_thread.start()

    log.info(f"Starting web server on port {config.web_port}...")
    try:
        server.run()
    except OSError:
        # server.close() (above) closes the listening socket while
        # server.run()'s select() loop may be blocked on it -- waitress
        # surfaces that as a "Bad file descriptor" OSError rather than a
        # clean return. That's expected once we've asked for a shutdown;
        # anything else is a real failure and should still propagate.
        if not shutting_down.is_set():
            raise
        log.info("Web server stopped.")
    finally:
        # Give the monitor thread a chance to finish process_new_emails()
        # and log out of IMAP cleanly before the process exits.
        monitor_thread.join(timeout=15)


if __name__ == "__main__":
    main()
