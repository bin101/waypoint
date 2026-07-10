"""Flask application: serves the public LiveTrack page plus a small
Basic-Auth-protected admin interface for status and manual overrides.

The public page (/) is intentionally unauthenticated -- it is the shareable
link people follow to watch the activity. Only /admin* requires credentials,
and is mounted at all only once ADMIN_USER + ADMIN_PASSWORD are configured.
"""

import functools
import json
import secrets
from datetime import datetime, timezone
from html import escape
from typing import Callable, Optional

from flask import Flask, Response, abort, jsonify, redirect, request, url_for

from .config import Config
from .email_monitor import LIVETRACK_LINK_RE
from .link_probe import probe_iframe_embeddable
from .state import AppState

# How often the "no active session" placeholder polls for a session having
# started, so a viewer who opens the shared link early doesn't have to
# manually reload once one goes live.
OFFLINE_REFRESH_SECONDS = 30


def _js_string_literal(value: str) -> str:
    """JSON-encode `value` for safe embedding inside an inline <script> block.

    json.dumps alone is not enough: it doesn't escape '<' or '>', so a value
    containing the literal text "</script>" would close the surrounding
    <script> tag at the HTML-parser level -- before any JS engine ever sees
    the string -- regardless of it being inside a quoted JS string. Escaping
    '<', '>' and '&' to \\u escapes (the same fix Django's `json_script`
    applies) neutralizes that without changing the decoded JS value.
    """
    encoded = json.dumps(value)
    return encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_page(link: Optional[str], *, use_iframe: bool, countdown: int, now: datetime) -> str:
    """Render the public HTML page.

    Three variants: an "offline" placeholder when no session is currently
    active, a redirect page with a JS countdown (default), or an iframe
    embed with a JS-based fallback link if the iframe fails to load.

    `link` is HTML-escaped for attribute contexts (`escape(..., quote=True)`)
    and encoded via `_js_string_literal` for the inline-JS context before
    being interpolated -- it may come from the admin override, which (unlike
    the email-extracted link) isn't guaranteed to match LIVETRACK_LINK_RE, so
    it can't be trusted to be free of quotes/angle brackets.
    """
    timestamp = now.strftime("%m/%d/%Y %H:%M:%S")

    if not link:
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="{OFFLINE_REFRESH_SECONDS}">
<title>Garmin LiveTrack</title>
<style>
body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f5f5f5; }}
.container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
.logo {{ color: #007cc3; font-size: 24px; font-weight: bold; margin-bottom: 20px; }}
.info {{ color: #666; font-size: 14px; margin-top: 20px; }}
</style>
</head>
<body>
<div class="container">
<div class="logo">Garmin LiveTrack</div>
<h2>No active session</h2>
<p>This page updates automatically as soon as a new LiveTrack session is detected.</p>
<div class="info"><p>Last checked: {timestamp}</p></div>
</div>
</body>
</html>"""

    link_attr = escape(link, quote=True)
    link_js = _js_string_literal(link)

    if use_iframe:
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Garmin LiveTrack</title>
<style>
body {{ margin: 0; padding: 0; font-family: Arial, sans-serif; }}
#track {{ display: block; border: none; width: 100%; height: 100vh; }}
.fallback {{ display: none; text-align: center; padding: 50px; background-color: #f5f5f5; }}
.fallback.show {{ display: block; }}
</style>
<script>
function handleIframeError() {{
  document.getElementById('track').style.display = 'none';
  document.getElementById('fallback').classList.add('show');
}}
window.onload = function() {{
  setTimeout(function() {{
    try {{
      var iframe = document.getElementById('track');
      if (!iframe.contentDocument && !iframe.contentWindow) {{
        handleIframeError();
      }}
    }} catch (e) {{
      handleIframeError();
    }}
  }}, 3000);
}};
</script>
</head>
<body>
<iframe id="track" src="{link_attr}" onerror="handleIframeError()"></iframe>
<div id="fallback" class="fallback">
<h2>Garmin LiveTrack</h2>
<p>The LiveTrack session cannot be embedded directly.</p>
<p><a href="{link_attr}" target="_blank">Open LiveTrack in a new tab</a></p>
<p style="color: #666; font-size: 14px;">Last updated: {timestamp}</p>
</div>
</body>
</html>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Garmin LiveTrack - Redirecting</title>
<meta http-equiv="refresh" content="{countdown}; url={link_attr}">
<style>
body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f5f5f5; }}
.container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
.logo {{ color: #007cc3; font-size: 24px; font-weight: bold; margin-bottom: 20px; }}
.link {{ color: #007cc3; text-decoration: none; font-size: 18px; background: #e8f4fd; padding: 15px 25px; border-radius: 5px; display: inline-block; margin: 20px 0; }}
.info {{ color: #666; font-size: 14px; margin-top: 20px; }}
.countdown {{ color: #007cc3; font-size: 18px; font-weight: bold; margin: 10px 0; }}
</style>
<script>
let countdown = {countdown};
function updateCountdown() {{
  document.getElementById('countdown').textContent = countdown;
  countdown--;
  if (countdown < 0) {{
    window.location.href = {link_js};
  }} else {{
    setTimeout(updateCountdown, 1000);
  }}
}}
window.onload = function() {{ updateCountdown(); }};
</script>
</head>
<body>
<div class="container">
<div class="logo">Garmin LiveTrack</div>
<h2>Redirecting to LiveTrack...</h2>
<div class="countdown">Redirecting in <span id="countdown">{countdown}</span> seconds</div>
<p>If the automatic redirect doesn't work:</p>
<a href="{link_attr}" class="link" target="_blank">Open LiveTrack</a>
<div class="info"><p>Last updated: {timestamp}</p></div>
</div>
</body>
</html>"""


def _render_admin_page(state: AppState) -> str:
    rows = "".join(
        f"<tr><td>{escape(entry['at'])}</td><td>{escape(entry['source'])}</td>"
        f"<td>{escape(entry['link'])}</td></tr>"
        for entry in reversed(state.history)
    )
    current_link = escape(state.current_link) if state.current_link else "(none)"
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Garmin LiveTrack - Admin</title>
<style>
body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 16px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
td, th {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 14px; }}
input[type=text] {{ width: 420px; padding: 6px; }}
button {{ padding: 6px 14px; }}
</style>
</head>
<body>
<h1>Garmin LiveTrack - Admin</h1>
<p><strong>Current link:</strong> {current_link}</p>
<p><strong>Updated at:</strong> {state.updated_at.isoformat() if state.updated_at else '(never)'}</p>
<p><strong>IMAP connected:</strong> {state.imap_connected}</p>
<p><strong>Last check:</strong> {state.last_check.isoformat() if state.last_check else '(never)'}</p>
<p><strong>Last error:</strong> {escape(state.last_error) if state.last_error else '(none)'}</p>

<h2>Manual override</h2>
<form method="post" action="/admin/link">
<input type="text" name="link" placeholder="https://livetrack.garmin.com/session/.../token/...">
<button type="submit">Set link</button>
</form>
<form method="post" action="/admin/clear">
<button type="submit">Clear current link</button>
</form>

<h2>Recent history</h2>
<table>
<tr><th>Time</th><th>Source</th><th>Link</th></tr>
{rows}
</table>
</body>
</html>"""


def create_app(
    config: Config, state: AppState, *, probe: Optional[Callable[[str], bool]] = None
) -> Flask:
    """Build the Flask app.

    `probe` defaults to the real network-based embeddability check
    (waypoint.link_probe.probe_iframe_embeddable); tests inject a stub so
    they never make real HTTP requests.
    """
    probe = probe or probe_iframe_embeddable
    app = Flask(__name__)

    def _check_auth(username: str, password: str) -> bool:
        # constant-time compares -- a `==` short-circuits on the first
        # mismatching byte, which is timing-observable over enough requests.
        return secrets.compare_digest(username, config.admin_user) and secrets.compare_digest(
            password, config.admin_password
        )

    def require_admin_auth(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not config.admin_enabled:
                # No admin credentials configured -> the admin surface does
                # not exist at all, rather than existing but unauthenticated.
                abort(404)
            auth = request.authorization
            if not auth or not _check_auth(auth.username, auth.password):
                return Response(
                    "Authentication required",
                    401,
                    {"WWW-Authenticate": 'Basic realm="LiveTrack Admin"'},
                )
            return view(*args, **kwargs)

        return wrapped

    @app.route("/")
    def public_page():
        html = render_page(
            state.current_link,
            use_iframe=bool(state.iframe_ok),
            countdown=config.redirect_countdown,
            now=datetime.now(timezone.utc),
        )
        return Response(html, mimetype="text/html")

    @app.route("/healthz")
    def healthz():
        return jsonify(
            {
                "imap_connected": state.imap_connected,
                "has_link": state.current_link is not None,
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
                "last_check": state.last_check.isoformat() if state.last_check else None,
                "last_error": state.last_error,
            }
        )

    @app.route("/admin")
    @require_admin_auth
    def admin_dashboard():
        return Response(_render_admin_page(state), mimetype="text/html")

    @app.route("/admin/link", methods=["POST"])
    @require_admin_auth
    def admin_set_link():
        link = request.form.get("link", "").strip()
        if not link:
            return redirect(url_for("admin_dashboard"))
        if not LIVETRACK_LINK_RE.fullmatch(link):
            # Reject rather than store: render_page HTML-escapes/JSON-encodes
            # whatever ends up here regardless, but restricting the admin
            # override to the same shape as an extracted email link also
            # closes off using it to probe/redirect to arbitrary internal
            # URLs (see probe_iframe_embeddable).
            return Response(
                "Invalid link: expected a "
                "https://livetrack.garmin.com/session/.../token/... URL",
                400,
            )
        state.set_link(link, source="admin", iframe_ok=probe(link))
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/clear", methods=["POST"])
    @require_admin_auth
    def admin_clear_link():
        state.clear_link()
        return redirect(url_for("admin_dashboard"))

    return app
