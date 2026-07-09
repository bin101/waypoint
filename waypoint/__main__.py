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
from waitress import serve

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

    def handle_signal(signum, frame):
        log.info("Shutting down Garmin LiveTrack...")
        monitor.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    monitor_thread = threading.Thread(target=monitor.run, name="email-monitor", daemon=True)
    monitor_thread.start()

    app = create_app(config, state)
    log.info(f"Starting web server on port {config.web_port}...")
    serve(app, host="0.0.0.0", port=config.web_port)


if __name__ == "__main__":
    main()
