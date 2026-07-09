"""Tests for the pure HTML renderer in waypoint.web."""

import datetime as dt
import json

from waypoint.web import OFFLINE_REFRESH_SECONDS, render_page

LINK = "https://livetrack.garmin.com/session/xxxx/token/YYYY"
NOW = dt.datetime(2026, 7, 9, 12, 0, 0)

# Not something the email extractor or the (validating) admin route would
# ever hand render_page, but render_page itself must still neutralize it --
# defense in depth, since it's a pure function with no knowledge of the
# caller.
MALICIOUS_LINK = 'https://evil.example/"><script>alert(1)</script>'


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
    # Some human-readable indication that there is currently no active session.
    assert "no active" in html.lower() or "offline" in html.lower()


def test_offline_page_polls_for_a_session_starting():
    # The copy promises this page updates itself once a session goes live --
    # it needs an actual refresh mechanism to back that up, otherwise a
    # viewer who opens the link early is stuck until they manually reload.
    html = render_page(None, use_iframe=False, countdown=10, now=NOW)

    assert f'<meta http-equiv="refresh" content="{OFFLINE_REFRESH_SECONDS}">' in html


def test_iframe_page_escapes_link_in_html_attributes():
    html = render_page(MALICIOUS_LINK, use_iframe=True, countdown=10, now=NOW)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_redirect_page_escapes_link_in_meta_and_encodes_in_js():
    html = render_page(MALICIOUS_LINK, use_iframe=False, countdown=10, now=NOW)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # Plain json.dumps alone would still leak the literal "</script>" into
    # the page, which closes the surrounding <script> tag at the
    # HTML-parser level regardless of JS string quoting -- '<'/'>' must be
    # escaped to </> in the JS-string context too.
    assert "</script>" in json.dumps(MALICIOUS_LINK)  # sanity: the raw case would be unsafe
    assert "\\u003cscript\\u003e" in html
