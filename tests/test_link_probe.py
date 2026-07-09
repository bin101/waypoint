"""Tests for waypoint.link_probe: deciding whether a LiveTrack link can be
embedded in an iframe, or must fall back to a plain redirect.

Client-side JS cannot reliably detect that a cross-origin page refused to be
framed (a blocked iframe just renders blank; no onerror fires and
contentDocument/contentWindow access doesn't distinguish "blocked" from
"loading"). So the decision is made server-side, once per link, by
inspecting the response headers Garmin actually sends.
"""

from unittest.mock import MagicMock, patch

from waypoint.link_probe import evaluate_frame_policy, probe_iframe_embeddable


def test_no_restrictive_headers_allows_embedding():
    assert evaluate_frame_policy({}) is True


def test_x_frame_options_deny_blocks_embedding():
    assert evaluate_frame_policy({"X-Frame-Options": "DENY"}) is False


def test_x_frame_options_sameorigin_blocks_embedding():
    # We are never "the same origin" as livetrack.garmin.com.
    assert evaluate_frame_policy({"X-Frame-Options": "SAMEORIGIN"}) is False


def test_header_lookup_is_case_insensitive():
    assert evaluate_frame_policy({"x-frame-options": "deny"}) is False


def test_csp_frame_ancestors_none_blocks_embedding():
    headers = {"Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'"}
    assert evaluate_frame_policy(headers) is False


def test_csp_frame_ancestors_self_blocks_embedding():
    headers = {"Content-Security-Policy": "frame-ancestors 'self'"}
    assert evaluate_frame_policy(headers) is False


def test_csp_frame_ancestors_wildcard_allows_embedding():
    headers = {"Content-Security-Policy": "frame-ancestors *"}
    assert evaluate_frame_policy(headers) is True


def test_csp_without_frame_ancestors_directive_allows_embedding():
    headers = {"Content-Security-Policy": "default-src 'self'"}
    assert evaluate_frame_policy(headers) is True


def _fake_response(headers):
    response = MagicMock()
    response.headers = headers
    response.status_code = 200
    return response


def test_probe_returns_true_for_permissive_response():
    with patch("waypoint.link_probe.requests.get", return_value=_fake_response({})) as get:
        assert probe_iframe_embeddable("https://livetrack.garmin.com/session/a/token/1") is True
        get.assert_called_once()
        assert get.call_args.kwargs.get("stream") is True


def test_probe_returns_false_for_restrictive_response():
    response = _fake_response({"X-Frame-Options": "DENY"})
    with patch("waypoint.link_probe.requests.get", return_value=response):
        assert probe_iframe_embeddable("https://livetrack.garmin.com/session/a/token/1") is False


def test_probe_closes_response_without_downloading_body():
    response = _fake_response({})
    with patch("waypoint.link_probe.requests.get", return_value=response):
        probe_iframe_embeddable("https://livetrack.garmin.com/session/a/token/1")
    response.close.assert_called_once()


def test_probe_fails_safe_to_redirect_on_network_error():
    with patch("waypoint.link_probe.requests.get", side_effect=OSError("boom")):
        assert probe_iframe_embeddable("https://livetrack.garmin.com/session/a/token/1") is False


def test_probe_fails_safe_to_redirect_on_non_2xx_status():
    response = _fake_response({})
    response.status_code = 403
    with patch("waypoint.link_probe.requests.get", return_value=response):
        assert probe_iframe_embeddable("https://livetrack.garmin.com/session/a/token/1") is False
