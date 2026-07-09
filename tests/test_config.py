"""Tests for IMAP security-mode validation in waypoint.config."""

import pytest

from waypoint.config import resolve_imap_security


def test_rejects_ssl_and_starttls_both_enabled():
    with pytest.raises(ValueError):
        resolve_imap_security(ssl=True, starttls=True)


@pytest.mark.parametrize(
    "ssl, starttls, expected",
    [
        (True, False, "SSL/TLS"),
        (False, True, "STARTTLS"),
        (False, False, "Unencrypted"),
    ],
)
def test_resolves_valid_combinations(ssl, starttls, expected):
    assert resolve_imap_security(ssl=ssl, starttls=starttls) == expected
