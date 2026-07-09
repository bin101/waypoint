"""Tests for the Flask app in waypoint.web: public page, health check, admin."""

import base64

import pytest

from waypoint.config import Config
from waypoint.state import AppState
from waypoint.web import create_app


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _make_config(tmp_path, *, admin_user="admin", admin_password="s3cret"):
    return Config(
        imap_server="imap.example.com",
        imap_port=993,
        imap_ssl=True,
        imap_starttls=False,
        email_user="user@example.com",
        email_pass="secret",
        trash_folder="Trash",
        healthcheck_url=None,
        redirect_countdown=10,
        web_port=8080,
        admin_user=admin_user,
        admin_password=admin_password,
        state_dir=tmp_path,
        log_level="INFO",
    )


@pytest.fixture
def config(tmp_path):
    return _make_config(tmp_path)


@pytest.fixture
def state(tmp_path):
    return AppState(state_dir=tmp_path)


@pytest.fixture
def probe_always_true():
    """Stub embeddability probe so tests never make real network calls."""
    return lambda link: True


@pytest.fixture
def client(config, state, probe_always_true):
    app = create_app(config, state, probe=probe_always_true)
    app.config.update(TESTING=True)
    return app.test_client()


def test_public_page_shows_offline_placeholder_when_no_link(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert b"livetrack.garmin.com" not in resp.data


def test_public_page_shows_current_link(client, state):
    state.set_link("https://livetrack.garmin.com/session/a/token/1", source="email", iframe_ok=False)

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"https://livetrack.garmin.com/session/a/token/1" in resp.data


def test_public_page_uses_iframe_when_link_is_embeddable(client, state):
    state.set_link("https://livetrack.garmin.com/session/a/token/1", source="email", iframe_ok=True)

    resp = client.get("/")

    assert b'<iframe id="track" src="https://livetrack.garmin.com/session/a/token/1"' in resp.data


def test_public_page_uses_redirect_when_link_is_not_embeddable(client, state):
    state.set_link("https://livetrack.garmin.com/session/a/token/1", source="email", iframe_ok=False)

    resp = client.get("/")

    assert b'<meta http-equiv="refresh"' in resp.data


def test_healthz_returns_200_with_status_json(client):
    resp = client.get("/healthz")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "imap_connected" in body
    assert "has_link" in body


def test_admin_requires_authentication(client):
    resp = client.get("/admin")

    assert resp.status_code == 401


def test_admin_accessible_with_valid_basic_auth(client):
    resp = client.get("/admin", headers=_basic_auth_header("admin", "s3cret"))

    assert resp.status_code == 200


def test_admin_rejects_wrong_credentials(client):
    resp = client.get("/admin", headers=_basic_auth_header("admin", "wrong"))

    assert resp.status_code == 401


def test_admin_can_set_link(client, state):
    resp = client.post(
        "/admin/link",
        data={"link": "https://livetrack.garmin.com/session/b/token/2"},
        headers=_basic_auth_header("admin", "s3cret"),
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert state.current_link == "https://livetrack.garmin.com/session/b/token/2"

    public_resp = client.get("/")
    assert b"https://livetrack.garmin.com/session/b/token/2" in public_resp.data


def test_admin_set_link_probes_embeddability(config, state):
    probe = lambda link: False  # noqa: E731 -- deliberately restrictive for this test
    app = create_app(config, state, probe=probe)
    app.config.update(TESTING=True)
    client = app.test_client()

    client.post(
        "/admin/link",
        data={"link": "https://livetrack.garmin.com/session/b/token/2"},
        headers=_basic_auth_header("admin", "s3cret"),
    )

    assert state.iframe_ok is False


def test_admin_set_link_rejects_non_livetrack_url(client, state):
    resp = client.post(
        "/admin/link",
        data={"link": "javascript:alert(1)"},
        headers=_basic_auth_header("admin", "s3cret"),
    )

    assert resp.status_code == 400
    assert state.current_link is None


def test_admin_set_link_rejects_non_garmin_domain(client, state):
    resp = client.post(
        "/admin/link",
        data={"link": "https://evil.example/session/a/token/1"},
        headers=_basic_auth_header("admin", "s3cret"),
    )

    assert resp.status_code == 400
    assert state.current_link is None


def test_admin_can_clear_link(client, state):
    state.set_link("https://livetrack.garmin.com/session/c/token/3", source="email", iframe_ok=True)

    resp = client.post(
        "/admin/clear",
        headers=_basic_auth_header("admin", "s3cret"),
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert state.current_link is None
    assert state.iframe_ok is None


def test_admin_disabled_when_credentials_not_configured(tmp_path):
    config = _make_config(tmp_path, admin_user=None, admin_password=None)
    state = AppState(state_dir=tmp_path)
    app = create_app(config, state, probe=lambda link: True)
    client = app.test_client()

    resp = client.get("/admin")

    assert resp.status_code == 404
