"""Thread-safe application state shared between the background IMAP monitor
thread and the web server, with a JSON file backing so the currently active
link survives a container restart."""

import json
import logging
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("waypoint.state")

MAX_HISTORY = 25
STATE_FILENAME = "state.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppState:
    """In-memory state, optionally persisted to <state_dir>/state.json."""

    def __init__(self, state_dir: Path):
        self._lock = threading.Lock()
        self.state_dir = Path(state_dir)
        self.current_link: Optional[str] = None
        self.updated_at: Optional[datetime] = None
        self.iframe_ok: Optional[bool] = None
        self.imap_connected: bool = False
        self.last_check: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.history: List[dict] = []

    @property
    def _state_file(self) -> Path:
        return self.state_dir / STATE_FILENAME

    def set_link(self, link: str, *, source: str, iframe_ok: bool = False) -> None:
        """Record a new active link (from the email monitor or the admin UI).

        `iframe_ok` reflects whether waypoint.link_probe determined this
        specific link can be embedded in an iframe; the public page falls
        back to a plain redirect whenever it's False.
        """
        with self._lock:
            now = _utcnow()
            self.current_link = link
            self.updated_at = now
            self.iframe_ok = iframe_ok
            self.history.append(
                {"link": link, "source": source, "iframe_ok": iframe_ok, "at": now.isoformat()}
            )
            if len(self.history) > MAX_HISTORY:
                self.history = self.history[-MAX_HISTORY:]
        self.save()

    def clear_link(self) -> None:
        """Clear the active link (e.g. an admin marking the session as over)."""
        with self._lock:
            self.current_link = None
            self.iframe_ok = None
            self.updated_at = _utcnow()
        self.save()

    def record_check(self, *, connected: bool, error: Optional[str]) -> None:
        """Update IMAP connectivity status, surfaced via /healthz and /admin."""
        with self._lock:
            self.imap_connected = connected
            self.last_check = _utcnow()
            self.last_error = error

    def save(self) -> None:
        """Persist the durable subset of state via write-then-rename, so a
        crash mid-write can never leave a corrupt state file behind."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {
                    "current_link": self.current_link,
                    "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                    "iframe_ok": self.iframe_ok,
                    "history": self.history,
                }
            fd, tmp_path = tempfile.mkstemp(dir=self.state_dir, prefix=".state-", suffix=".tmp")
            try:
                with open(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                Path(tmp_path).replace(self._state_file)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise
        except Exception as e:
            log.warning(f"Could not persist state: {e}")

    def load(self) -> None:
        """Load previously persisted state, if any. Never raises -- a missing
        or corrupt state file just means starting from a clean slate."""
        if not self._state_file.exists():
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            with self._lock:
                self.current_link = payload.get("current_link")
                updated_at = payload.get("updated_at")
                self.updated_at = datetime.fromisoformat(updated_at) if updated_at else None
                self.iframe_ok = payload.get("iframe_ok")
                self.history = payload.get("history", [])
        except Exception as e:
            log.warning(f"Could not load persisted state: {e}")
