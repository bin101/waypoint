# Waypoint

A small self-contained service that watches an email inbox via IMAP IDLE for incoming Garmin
LiveTrack notification emails, extracts the LiveTrack session link, and serves it itself as a
public web page — so you get one fixed URL that always points to whichever LiveTrack session is
currently active. Ships as a single Docker image; no external file hosting (FTP, S3, ...) required.

## How it works

- A background thread keeps an IMAP IDLE connection open and reacts to new mail in near real time,
  with a periodic session renewal and reconnect-with-backoff as a safety net.
- Whenever a new link appears (from an email or an admin override), the server makes one HTTP
  request to it and inspects the `X-Frame-Options`/`Content-Security-Policy` response headers to
  decide whether the LiveTrack page can be embedded directly. See
  [Display mode](#display-mode-iframe-vs-redirect) below.
- A lightweight built-in web server (Flask + waitress) serves:
  - `GET /` — the public page: embeds the current LiveTrack link in an iframe if possible, falls
    back to a redirect page if not (or shows an "offline" placeholder if no session is active).
  - `GET /healthz` — JSON status for container health checks / uptime monitoring.
  - `GET /admin` — a Basic-Auth-protected dashboard: current link, IMAP status, recent history, and
    a manual set/clear override.
- The current link and a short history are persisted to a JSON file on a Docker volume, so a
  container restart keeps serving the last known link instead of going blank.

## Quick start

### Run with Docker Compose (recommended)

```bash
cp .env.example .env
# edit .env with your IMAP credentials and admin password
docker compose up -d
```

The service is now available at `http://localhost:8080/`, with the admin dashboard at
`http://localhost:8080/admin`. State is persisted to the `waypoint-data` named volume declared in
`docker-compose.yml`.

### Run with `docker run`

```bash
docker run -d \
  --name waypoint \
  --env-file .env \
  -p 8080:8080 \
  -v waypoint-data:/data \
  --restart unless-stopped \
  ghcr.io/bin101/waypoint:latest
```

### Pull from GitHub Container Registry

Pre-built multi-arch images (`linux/amd64` + `linux/arm64`) are published to GHCR on every tagged
release:

```bash
docker pull ghcr.io/bin101/waypoint:latest
# or a pinned version:
docker pull ghcr.io/bin101/waypoint:1.0.0
```

### Build locally

```bash
docker build -t waypoint .
# or:
docker compose build
```

Building the image runs the full test suite as part of the build (see [Development](#development)
below) — the build fails if any test fails, so a broken image can never be produced.

## Configuration

All configuration is via environment variables — copy `.env.example` to `.env` and adjust.

### IMAP

| Variable | Default | Description |
|---|---|---|
| `IMAP_SERVER` | — (required) | IMAP server hostname |
| `IMAP_PORT` | `993` | IMAP server port |
| `IMAP_SSL` | `true` | Use a direct SSL/TLS connection |
| `IMAP_STARTTLS` | `false` | Use STARTTLS (plain connection upgraded to TLS) |
| `EMAIL_USER` | — (required) | IMAP username |
| `EMAIL_PASS` | — (required) | IMAP password |
| `TRASH_FOLDER` | `Trash` | IMAP folder processed emails are moved to |

**`IMAP_SSL` and `IMAP_STARTTLS` are mutually exclusive** — enabling both is a configuration error
and the service will refuse to start.

| Security method | `IMAP_SSL` | `IMAP_STARTTLS` | Typical port | Notes |
|---|---|---|---|---|
| SSL/TLS (recommended) | `true` | `false` | 993 | Direct encrypted connection |
| STARTTLS | `false` | `true` | 993 | Plain connection upgraded to TLS |
| Unencrypted | `false` | `false` | 143 | Not recommended — testing/internal networks only |

Common providers: Gmail, Outlook/Hotmail and Yahoo all use port 993 with `IMAP_SSL=true`. Many
custom/corporate servers use port 993 with `IMAP_STARTTLS=true`.

### Monitoring & display

| Variable | Default | Description |
|---|---|---|
| `HEALTHCHECK_URL` | — (optional) | External URL pinged periodically (e.g. healthchecks.io) once the service is confirmed healthy |
| `REDIRECT_COUNTDOWN` | `10` | Seconds shown on the redirect fallback page before it navigates to the LiveTrack link (only used when the link can't be embedded — see below) |

### Web server & admin interface

| Variable | Default | Description |
|---|---|---|
| `WEB_PORT` | `8080` | Port the web server listens on inside the container |
| `ADMIN_USER` | — (optional) | Basic Auth username for `/admin` |
| `ADMIN_PASSWORD` | — (optional) | Basic Auth password for `/admin` |
| `STATE_DIR` | `/data` | Directory the current link/history are persisted to (point this at a mounted volume) |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

The admin interface at `/admin` is only mounted if **both** `ADMIN_USER` and `ADMIN_PASSWORD` are
set — otherwise it responds `404` rather than existing unauthenticated. The public page (`GET /`)
is intentionally unauthenticated, since it's the link you actually share.

## Admin interface

At `/admin` (Basic Auth):
- **Status dashboard** — current link, when it was last updated, IMAP connection status, last
  error.
- **Manual override** — set the current link directly, or clear it (e.g. to show the "offline"
  page when no activity is live). The link must match the same
  `https://livetrack.garmin.com/session/.../token/...` shape as an email-extracted one (`400` on
  anything else).
- **Recent history** — the last 20 links that were published, with timestamp and source
  (`email` or `admin`), including whether each was served as an iframe or a redirect.

## Display mode: iframe vs. redirect

There is no manual toggle for this — it's decided automatically, per link, and always falls back
to something that works:

1. When a link is set (from an email or the admin override), the server makes one HTTP request to
   it and checks the `X-Frame-Options` and `Content-Security-Policy: frame-ancestors` response
   headers.
2. If neither header forbids framing by a third-party site, `/` embeds the LiveTrack page directly
   in an iframe.
3. Otherwise — or if the probe request fails or times out for any reason — `/` serves the redirect
   page instead (a short countdown, then `window.location` navigates to the LiveTrack link).

This split exists because a browser can't reliably tell *from inside the parent page* that a
cross-origin iframe was blocked: a blocked frame just renders blank, no `onerror` fires, and
`contentWindow`/`contentDocument` don't distinguish "blocked" from "still loading". Making the
decision server-side, from the actual response headers, is the only reliable way to know in
advance.

## Persistence

The current link and history are written to `STATE_DIR/state.json`. `docker-compose.yml` mounts
this to the named volume `waypoint-data` by default. If you'd rather bind-mount a host directory
instead, mount it to `/data` and make sure it's writable by UID/GID `1000` (the non-root user the
container runs as), e.g.:

```bash
mkdir -p ./data && sudo chown 1000:1000 ./data
```

## Development

Requires Python 3.12+.

```bash
pip install -r requirements-dev.txt
pytest
```

The test suite covers the pure link-extraction and page-rendering logic (using a sanitized sample
Garmin LiveTrack email fixture, see `tests/fixtures/`), the persisted state, IMAP config validation,
and the web routes (via Flask's test client) — all without needing a real IMAP or SMTP server.

### Branching and releases

- `develop` is the integration branch — feature branches and day-to-day work target `develop` via
  PR. Pushes here (and PRs) only run the test suite (`.github/workflows/ci.yml`); nothing gets
  built or released.
- `main` only ever moves forward via a PR from `develop`. Every push to `main` runs
  [Release Please](https://github.com/googleapis/release-please)
  (`.github/workflows/release-please.yml`), which inspects the
  [Conventional Commits](https://www.conventionalcommits.org/) merged in since the last release and
  keeps a standing **release PR** up to date with the accumulated version bump and `CHANGELOG.md`
  entry — it never pushes to `main` directly.
- Merging that release PR (like any other PR, via review) is what actually cuts the `vX.Y.Z` tag +
  GitHub Release. Only that tag push triggers `.github/workflows/publish.yml`, which builds the
  multi-arch image and publishes it to GHCR — a plain commit landing on `main` never does.
- Commit messages need a Conventional Commits prefix for Release Please to pick them up correctly:
  `fix:` → patch, `feat:` → minor, `BREAKING CHANGE:` (in the footer) → major. Anything else
  (`chore:`, `docs:`, `test:`, ...) is included in the changelog but doesn't bump the version.

## Troubleshooting

**Browser console shows `net::ERR_BLOCKED_BY_CLIENT`.** This is not a server/reverse-proxy issue —
Waypoint doesn't ship or require any nginx config, and this error never comes from the server; the
browser generates it locally, before the request even leaves the machine. It means a client-side
ad/tracker blocker (uBlock Origin, Brave Shields, Pi-hole-style browser extensions, ...) intercepted
a request. Garmin's `livetrack.garmin.com` URLs contain the substring `track`, which matches generic
tracker-blocklist rules in some filter lists, so the extension blocks the iframe/redirect target
itself. Check the failing request's URL in the Network tab to confirm, then allow-list
`livetrack.garmin.com` in the blocker — there's nothing to change in Waypoint's own config for this.

## Security notes

- Credentials are only ever read from environment variables (typically via `.env`, which is
  gitignored) — never hardcode or log them.
- Use strong, unique passwords for both the mailbox and the admin interface.
- `/admin` is protected by HTTP Basic Auth; since Basic Auth sends credentials on every request,
  put the service behind TLS (a reverse proxy such as Traefik/Caddy/nginx) if it's reachable over
  the public internet.

## License

[MIT](LICENSE)
