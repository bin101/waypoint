"""Tests for the pure HTML renderer in waypoint.web."""

import datetime as dt

from waypoint.web import render_page

LINK = "https://livetrack.garmin.com/session/xxxx/token/YYYY"
NOW = dt.datetime(2026, 7, 9, 12, 0, 0)


def test_redirect_page_contains_link_meta_refresh_and_countdown():
    html = render_page(LINK, use_iframe=False, countdown=15, now=NOW)

    assert LINK in html
    assert '<meta http-equiv="refresh" content="15; url=' in html
    assert "15" in html  # visible countdown seconds


def test_iframe_page_embeds_link_in_iframe_with_fallback():
    html = render_page(LINK, use_iframe=True, countdown=10, now=NOW)

    assert f'src="{LINK}"' in html
    assert f'href="{LINK}"' in html  # fallback link if the iframe is blocked


def test_offline_page_when_no_link_is_available():
    html = render_page(None, use_iframe=False, countdown=10, now=NOW)

    assert LINK not in html
    assert "http-equiv=\"refresh\"" not in html
    # Some human-readable indication that there is currently no active session.
    assert "no active" in html.lower() or "offline" in html.lower()
