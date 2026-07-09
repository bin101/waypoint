"""Tests for waypoint.email_monitor.extract_livetrack_link.

The Garmin LiveTrack notification email is a multipart/related message whose
only body part is text/html (no text/plain fallback). This is exercised via
the sanitized fixture in tests/fixtures/testmail_garmin.eml -- a real Garmin
email with all personal/security-relevant data replaced, see CLAUDE.md.
"""

from pathlib import Path

import pytest

from waypoint.email_monitor import extract_livetrack_link

FIXTURES = Path(__file__).parent / "fixtures"

SANITIZED_LINK = (
    "https://livetrack.garmin.com/session/"
    "11111111-2222-3333-4444-555555555555/token/"
    "ABCDEF0123456789ABCDEF0123456789"
)


def test_extracts_link_from_html_only_garmin_email():
    raw = (FIXTURES / "testmail_garmin.eml").read_bytes()

    link = extract_livetrack_link(raw)

    assert link == SANITIZED_LINK


def test_extracts_link_from_plain_text_email():
    raw = (
        b"From: Garmin <noreply@garmin.com>\r\n"
        b"To: user@example.com\r\n"
        b"Subject: Watch Alex's LiveTrack\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"View the activity here: "
        b"https://livetrack.garmin.com/session/aaaa/token/DEADBEEF00\r\n"
    )

    link = extract_livetrack_link(raw)

    assert link == "https://livetrack.garmin.com/session/aaaa/token/DEADBEEF00"


def test_link_is_reassembled_across_quoted_printable_soft_breaks():
    # A quoted-printable "=\n" soft line break lands in the middle of the URL.
    raw = (
        b"From: Garmin <noreply@garmin.com>\r\n"
        b"To: user@example.com\r\n"
        b"Subject: LiveTrack\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n"
        b"\r\n"
        b'<a href=3D"https://livetrack.garmin.com/session/bbbb/tok=\r\n'
        b'en/CAFEBABE00">link</a>\r\n'
    )

    link = extract_livetrack_link(raw)

    assert link == "https://livetrack.garmin.com/session/bbbb/token/CAFEBABE00"


def test_token_matching_is_case_insensitive():
    raw = (
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"https://livetrack.garmin.com/session/cccc/token/deadBEEF00\r\n"
    )

    link = extract_livetrack_link(raw)

    assert link == "https://livetrack.garmin.com/session/cccc/token/deadBEEF00"


def test_returns_none_when_no_livetrack_link_present():
    raw = (
        b"From: someone@example.com\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Just a regular email with no LiveTrack link at all.\r\n"
    )

    assert extract_livetrack_link(raw) is None
