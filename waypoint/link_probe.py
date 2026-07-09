"""Decide whether a LiveTrack link can be embedded in an iframe, or must fall
back to a plain redirect.

Client-side JavaScript cannot reliably detect that a cross-origin page
refused to be framed: a blocked iframe just renders blank, no `onerror`
fires, and `contentDocument`/`contentWindow` access doesn't distinguish
"blocked" from "still loading". So the decision is made once, server-side,
by inspecting the response headers Garmin actually sends for that link.
"""

import logging
from typing import Mapping

import requests

log = logging.getLogger("waypoint.link_probe")

PROBE_TIMEOUT_SECONDS = 5


def evaluate_frame_policy(headers: Mapping[str, str]) -> bool:
    """Return whether the given response headers permit being framed by us.

    We are always a different origin than livetrack.garmin.com, so any
    X-Frame-Options value (DENY, SAMEORIGIN, or the deprecated ALLOW-FROM)
    blocks us. For Content-Security-Policy, only an absent frame-ancestors
    directive or an explicit wildcard is treated as permissive -- anything
    naming specific origins (including 'self') cannot possibly include us.
    """
    lower_headers = {k.lower(): v for k, v in headers.items()}

    if lower_headers.get("x-frame-options"):
        return False

    csp = lower_headers.get("content-security-policy")
    if csp:
        for directive in csp.split(";"):
            directive = directive.strip()
            if directive.lower().startswith("frame-ancestors"):
                value = directive[len("frame-ancestors"):].strip()
                return value == "*"

    return True


def probe_iframe_embeddable(link: str) -> bool:
    """Fetch `link`'s response headers and decide if it can be embedded.

    Uses a streamed GET (some servers mishandle HEAD) and closes the
    connection immediately after reading headers, without downloading the
    response body. Fails safe to False (i.e. use the redirect fallback) on
    any network error or non-2xx status, since the redirect always works.
    """
    try:
        response = requests.get(link, timeout=PROBE_TIMEOUT_SECONDS, stream=True)
    except Exception as e:
        # Broad on purpose: any failure here must fall back to the redirect
        # page, which always works, rather than propagate and break email
        # processing or the admin "set link" action.
        log.warning(f"Could not probe {link} for iframe embeddability: {e}")
        return False

    try:
        if not (200 <= response.status_code < 300):
            log.warning(f"Embeddability probe for {link} got status {response.status_code}")
            return False
        return evaluate_frame_policy(response.headers)
    finally:
        response.close()
