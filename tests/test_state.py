"""Tests for the thread-safe, persisted AppState in waypoint.state."""

import datetime as dt

from waypoint.state import AppState, MAX_HISTORY


def test_set_link_updates_current_link_and_history(tmp_path):
    state = AppState(state_dir=tmp_path)

    state.set_link("https://livetrack.garmin.com/session/a/token/1", source="email", iframe_ok=True)

    assert state.current_link == "https://livetrack.garmin.com/session/a/token/1"
    assert state.updated_at is not None
    assert state.iframe_ok is True
    assert len(state.history) == 1
    assert state.history[0]["link"] == "https://livetrack.garmin.com/session/a/token/1"
    assert state.history[0]["source"] == "email"
    assert state.history[0]["iframe_ok"] is True


def test_set_link_defaults_to_redirect_when_iframe_ok_not_specified(tmp_path):
    state = AppState(state_dir=tmp_path)
    state.set_link("https://livetrack.garmin.com/session/a/token/1", source="email")

    assert state.iframe_ok is False


def test_clear_link_resets_current_link_history_and_iframe_ok(tmp_path):
    state = AppState(state_dir=tmp_path)
    state.set_link("https://livetrack.garmin.com/session/a/token/1", source="email", iframe_ok=True)

    state.clear_link()

    assert state.current_link is None
    assert state.iframe_ok is None
    assert len(state.history) == 1


def test_history_is_capped_at_max_history(tmp_path):
    state = AppState(state_dir=tmp_path)

    for i in range(MAX_HISTORY + 5):
        state.set_link(f"https://livetrack.garmin.com/session/{i}/token/x", source="email")

    assert len(state.history) == MAX_HISTORY
    # Most recent entry should be last.
    assert state.history[-1]["link"].endswith(f"{MAX_HISTORY + 4}/token/x")


def test_save_and_load_round_trip(tmp_path):
    state = AppState(state_dir=tmp_path)
    state.set_link("https://livetrack.garmin.com/session/a/token/1", source="email", iframe_ok=True)
    state.save()

    loaded = AppState(state_dir=tmp_path)
    loaded.load()

    assert loaded.current_link == "https://livetrack.garmin.com/session/a/token/1"
    assert loaded.iframe_ok is True
    assert len(loaded.history) == 1


def test_load_with_no_existing_state_file_is_a_noop(tmp_path):
    state = AppState(state_dir=tmp_path)

    state.load()  # must not raise even though state.json does not exist yet

    assert state.current_link is None
    assert state.iframe_ok is None
    assert state.history == []


def test_record_check_updates_imap_status_and_error(tmp_path):
    state = AppState(state_dir=tmp_path)

    state.record_check(connected=True, error=None)
    assert state.imap_connected is True
    assert state.last_error is None
    assert isinstance(state.last_check, dt.datetime)

    state.record_check(connected=False, error="connection reset")
    assert state.imap_connected is False
    assert state.last_error == "connection reset"
