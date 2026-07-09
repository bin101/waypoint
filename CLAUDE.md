# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Waypoint** is a small Docker-only service that watches an email inbox for Garmin LiveTrack
notification emails, extracts the LiveTrack session link, and **serves it itself** as a public web
page — a fixed URL that always points to whichever LiveTrack session is currently active. There is
no external upload step (an earlier version used FTP; that has been removed entirely).

The project is developed test-driven and distributed exclusively as a multi-arch (`amd64`+`arm64`)
Docker image published to `ghcr.io/bin101/waypoint`.

## Commands

```bash
# Run the test suite (no IMAP/Docker required — everything below is pure/mocked)
pip install -r requirements-dev.txt
pytest

# Build the image (this re-runs the full test suite as an embedded build stage;
# the build fails if any test fails)
docker build -t waypoint .

# Run it
cp .env.example .env   # fill in real IMAP + admin credentials
docker compose up -d
```

There is no bare `python -m waypoint` deployment path anymore — the service is meant to run
exclusively inside the container (see [Architecture](#architecture) for why: it depends on a
non-root filesystem layout and a mounted state volume).

## Architecture

### Module layout (`waypoint/` package)

- **`config.py`** — `Config` dataclass loaded from environment variables via `Config.from_env()`.
  `resolve_imap_security()` is a pure function enforcing that `IMAP_SSL`/`IMAP_STARTTLS` are
  mutually exclusive (raises `ValueError` otherwise) — keep this invariant if adding new IMAP
  connection modes.
- **`state.py`** — `AppState`: the single source of truth shared between the background email
  monitor and the web server, guarded by a lock since both run concurrently in different threads.
  Holds `current_link`, `updated_at`, `iframe_ok`, `imap_connected`, `last_check`, `last_error`, and
  a capped `history` (`MAX_HISTORY` entries, each recording `iframe_ok` too). `save()`/`load()`
  persist/restore the durable subset (`current_link`, `updated_at`, `iframe_ok`, `history`) to
  `<STATE_DIR>/state.json` via write-to-temp-file + atomic rename, so a crash mid-write can't
  corrupt the state file.
- **`link_probe.py`** — `probe_iframe_embeddable(link) -> bool` decides, per link, whether it can be
  embedded in an iframe: it makes one streamed `GET` request (closed immediately after reading
  headers, body never downloaded) and hands the response headers to the pure
  `evaluate_frame_policy(headers) -> bool`, which checks `X-Frame-Options` (any value blocks us,
  since we're never same-origin as `livetrack.garmin.com`) and `Content-Security-Policy:
  frame-ancestors` (only an absent directive or an explicit `*` is treated as permissive). Fails
  safe to `False` on any network error or non-2xx status — the redirect fallback always works, so
  when in doubt, use it. This exists because a browser cannot reliably detect from the parent page
  that a cross-origin iframe was blocked (no `onerror`, `contentWindow` still exists) — the decision
  has to be made server-side, in advance, from the actual response headers.
- **`email_monitor.py`** — `extract_livetrack_link(raw_email: bytes) -> str | None` is the pure,
  side-effect-free extraction logic (see [Known gotcha](#known-gotcha-html-only-emails) below), and
  `EmailMonitor` is the stateful IMAP IDLE loop (connect → initial UNSEEN sweep → IDLE, with session
  renewal every 20 minutes and reconnect-with-backoff on failure). Runs in a background daemon
  thread started from `__main__.py`; on each processed email it calls `probe_iframe_embeddable(link)`
  and then `state.set_link(link, source="email", iframe_ok=...)` instead of uploading anything
  anywhere.
- **`web.py`** — `create_app(config, state, probe=None)` builds the Flask app (`probe` defaults to
  `probe_iframe_embeddable`; tests inject a stub so they never hit the network); `render_page(...)`
  is the pure HTML renderer (redirect page / iframe page / "offline" placeholder, selected by
  `use_iframe`/whether a link is set — `public_page` passes `use_iframe=bool(state.iframe_ok)`, i.e.
  the per-link probe result, not a global setting). Routes: `GET /` (public), `GET /healthz` (JSON
  status), `GET /admin` + `POST /admin/link` (calls `probe(link)` before storing) +
  `POST /admin/clear` (Basic Auth, mounted only when `config.admin_enabled` — i.e. both
  `ADMIN_USER` and `ADMIN_PASSWORD` are set; otherwise `404`).
- **`__main__.py`** (`python -m waypoint`) — wires it all together: loads config, validates
  credentials (exits with a clear error if missing), loads persisted state, starts the
  `EmailMonitor` in a background thread, and serves the Flask app via `waitress` in the foreground.
  `SIGINT`/`SIGTERM` call `monitor.stop()` for a clean IMAP logout.

The web server and the email monitor are independent failure domains: an IMAP connection failure
does not crash or block the web server (verified by running the container against an invalid IMAP
host — `/` and `/healthz` still respond `200`, `/healthz` just reports `imap_connected: false`).

### Known gotcha: HTML-only emails

Garmin's current LiveTrack notification template is `multipart/related` with **only a
`text/html`** body part — there is no `text/plain` fallback. `extract_livetrack_link` therefore
walks **both** `text/plain` and `text/html` parts (via `get_payload(decode=True)`, which resolves
quoted-printable/base64 and reassembles soft line breaks before the regex runs) and matches the
token case-insensitively. This is exercised by `tests/test_extract_link.py` against a **sanitized**
real-world fixture at `tests/fixtures/testmail_garmin.eml`. If you ever regenerate that fixture from a real email, **you
must sanitize it first**: replace the recipient address, mail-host identifiers, names, profile-image
IDs, and — most importantly — the live LiveTrack session/token and the unsubscribe token, all with
placeholder values, while preserving the MIME structure (multipart/related, HTML-only,
quoted-printable with soft line breaks) that the extraction logic is actually being tested against.
Never commit a raw `.eml` capture; `.gitignore` blocks `*.eml` outside `tests/fixtures/`.

### Docker build

`Dockerfile` is a multi-stage build: `deps` (installs `requirements.txt` into `/install`) → `test`
(installs `requirements-dev.txt`, copies `waypoint/` + `tests/`, runs `pytest -q` — this stage
failing fails the whole build) → `runtime` (non-root user `waypoint` at fixed UID/GID `1000`,
`COPY --from=test /app/waypoint ...` so the runtime image only ever contains code that just passed
the test stage, never test files themselves). `HEALTHCHECK` hits `/healthz` with a Python
`urllib` one-liner (no `curl`/`wget` in `python:3.12-slim`).

### Branching, versioning & release pipeline

`develop` is the integration branch (feature branches → PR → `develop`); `main` only moves via a
PR from `develop`. Three separate workflows split the concerns:

- `.github/workflows/ci.yml` — the `test` job, on every push (`main`/`develop`) and every PR.
- `.github/workflows/release-please.yml` — runs
  [Release Please](https://github.com/googleapis/release-please) (config:
  `release-please-config.json` + `.release-please-manifest.json`) on every push to `main`. It never
  pushes to `main` directly: it maintains a standing release PR (version bump + `CHANGELOG.md`)
  from Conventional Commits (`fix:`, `feat:`, `BREAKING CHANGE:`) and only cuts the `vX.Y.Z` tag +
  GitHub Release once that PR is actually merged.
- `.github/workflows/publish.yml` — triggers only on a `vX.Y.Z` tag push (i.e. only once
  release-please has cut a release) and does the multi-arch Docker build + push to GHCR.

The version lives in `waypoint/__init__.py` (`__version__`, marked with a trailing
`# x-release-please-version` comment that release-please's generic file updater matches on) — don't
hand-edit it or hand-create release tags, or the version, the tag, and `CHANGELOG.md` drift apart.

## Invariants to preserve when editing

- Every branch through `EmailMonitor.process_new_emails` must still move the processed UID to
  `TRASH_FOLDER`, even on error — otherwise the same email gets reprocessed on every IDLE tick.
- `send_healthcheck()` must not be called on any error path — it's the signal an external monitor
  uses to detect the service is stuck.
- Keep `IMAP_SSL`/`IMAP_STARTTLS` mutually exclusive validation in `resolve_imap_security` if adding
  new connection modes.
- Credentials only ever come from environment variables (`.env`, gitignored) — don't hardcode or
  log secrets.
- `/admin*` routes must stay behind `require_admin_auth` and must 404 (not just 401) when
  `ADMIN_USER`/`ADMIN_PASSWORD` are unset — the admin surface shouldn't exist at all without
  credentials configured.
- Any change to `AppState` history/link handling should keep `save()` being called after every
  mutation, so a restart doesn't lose recent state.
- `probe_iframe_embeddable` must never raise and must default to `False` (redirect) on any
  ambiguity (network error, timeout, non-2xx, unparseable header) — the redirect page is the one
  path that always works, so failure must never accidentally select the iframe path instead.
- `render_page` must keep escaping `link` for HTML-attribute contexts (`html.escape(..., quote=True)`
  → `link_attr`) and JSON-encoding it for the inline-JS context (`json.dumps` → `link_js`) — never
  interpolate the raw string again. The admin override isn't guaranteed to match
  `LIVETRACK_LINK_RE`, so this is the only thing standing between an admin-set value containing
  quotes/`<>` and breaking out of an attribute or the JS string literal.
- `/admin/link` must keep rejecting (`400`) any submitted link that doesn't fullmatch
  `LIVETRACK_LINK_RE` before it ever reaches `state.set_link`/`probe()` — this is what keeps the
  admin override from being usable to probe/redirect to arbitrary internal URLs.
- `_check_auth` must keep using `secrets.compare_digest`, not `==`, for both the username and
  password comparison.
- `__main__.py` starts the server via `waitress.create_server(...)` + `server.run()`, not the
  `serve()` shortcut — the signal handler needs a `server` handle to call `.close()` on. Without
  it, SIGTERM is silently swallowed by `handle_signal` and the container only stops on Docker's
  SIGKILL, skipping `EmailMonitor`'s clean IMAP logout.
- The `Dockerfile`'s `HEALTHCHECK` reads `WEB_PORT` from the environment at check time (defaulting
  to `8080`) rather than hard-coding a port — keep it that way if `WEB_PORT` stays configurable.
