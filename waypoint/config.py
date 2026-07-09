"""Configuration for the Garmin LiveTrack monitor, loaded from environment
variables (see .env.example for the full reference)."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _bool_env(value: str) -> bool:
    return value.lower() in ("true", "1", "yes", "on")


def resolve_imap_security(*, ssl: bool, starttls: bool) -> str:
    """Validate and name the configured IMAP security method.

    SSL/TLS and STARTTLS are mutually exclusive connection strategies -- a
    server is reached either via a direct encrypted connection or via a
    plaintext connection later upgraded to TLS, never both. Raises
    ValueError if both are requested at once.
    """
    if ssl and starttls:
        raise ValueError(
            "Conflicting IMAP security settings: IMAP_SSL and IMAP_STARTTLS "
            "cannot both be true. Use either SSL/TLS (IMAP_SSL=true, "
            "IMAP_STARTTLS=false) or STARTTLS (IMAP_SSL=false, "
            "IMAP_STARTTLS=true)."
        )
    if ssl:
        return "SSL/TLS"
    if starttls:
        return "STARTTLS"
    return "Unencrypted"


@dataclass
class Config:
    # IMAP
    imap_server: Optional[str]
    imap_port: int
    imap_ssl: bool
    imap_starttls: bool
    email_user: Optional[str]
    email_pass: Optional[str]
    trash_folder: str

    # Monitoring / display
    healthcheck_url: Optional[str]
    redirect_countdown: int

    # Web server / admin
    web_port: int
    admin_user: Optional[str]
    admin_password: Optional[str]

    # Persistence
    state_dir: Path

    log_level: str

    @property
    def admin_enabled(self) -> bool:
        """The admin interface is only mounted once both credentials are set."""
        return bool(self.admin_user) and bool(self.admin_password)

    def validate_credentials(self) -> None:
        """Raise ValueError if required credentials/config are missing or invalid."""
        required = {
            "EMAIL_USER": self.email_user,
            "EMAIL_PASS": self.email_pass,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing credentials: {', '.join(missing)}")
        resolve_imap_security(ssl=self.imap_ssl, starttls=self.imap_starttls)

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            imap_server=os.getenv("IMAP_SERVER"),
            imap_port=int(os.getenv("IMAP_PORT", "993")),
            imap_ssl=_bool_env(os.getenv("IMAP_SSL", "true")),
            imap_starttls=_bool_env(os.getenv("IMAP_STARTTLS", "false")),
            email_user=os.getenv("EMAIL_USER"),
            email_pass=os.getenv("EMAIL_PASS"),
            trash_folder=os.getenv("TRASH_FOLDER", "Trash"),
            healthcheck_url=os.getenv("HEALTHCHECK_URL") or None,
            redirect_countdown=int(os.getenv("REDIRECT_COUNTDOWN", "10")),
            web_port=int(os.getenv("WEB_PORT", "8080")),
            admin_user=os.getenv("ADMIN_USER") or None,
            admin_password=os.getenv("ADMIN_PASSWORD") or None,
            state_dir=Path(os.getenv("STATE_DIR", "/data")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
