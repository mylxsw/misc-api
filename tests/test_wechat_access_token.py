"""Tests for WeChat access-token acquisition and caching."""

from unittest.mock import patch

import pytest

from lib.wechat import wechat_api


@pytest.fixture(autouse=True)
def clear_token_cache():
    wechat_api._token_cache.clear()
    yield
    wechat_api._token_cache.clear()


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_stable_token_is_preferred_and_cached():
    with (
        patch.object(wechat_api.requests, "post") as post,
        patch.object(wechat_api.requests, "get") as get,
    ):
        post.return_value = FakeResponse(
            {"access_token": "stable-1", "expires_in": 7200}
        )

        assert wechat_api.get_access_token("appid", "secret") == "stable-1"
        assert wechat_api.get_access_token("appid", "secret") == "stable-1"

        assert post.call_count == 1
        assert get.call_count == 0
        assert post.call_args.args[0] == wechat_api.STABLE_TOKEN_URL


def test_stable_token_requests_same_token_across_workers():
    """A second process asking for the token must not invalidate the first one."""
    with patch.object(wechat_api.requests, "post") as post:
        post.return_value = FakeResponse({"access_token": "shared", "expires_in": 7200})

        first = wechat_api.get_access_token("appid", "secret")
        wechat_api._token_cache.clear()
        second = wechat_api.get_access_token("appid", "secret")

        assert first == second == "shared"
        assert post.call_args.kwargs["data"], "stable_token needs a JSON body"
        assert b'"force_refresh": false' in post.call_args.kwargs["data"]


def test_falls_back_to_classic_endpoint():
    with (
        patch.object(wechat_api.requests, "post") as post,
        patch.object(wechat_api.requests, "get") as get,
    ):
        post.return_value = FakeResponse({"errcode": -1, "errmsg": "system error"})
        get.return_value = FakeResponse(
            {"access_token": "classic-1", "expires_in": 7200}
        )

        assert wechat_api.get_access_token("appid", "secret") == "classic-1"
        assert get.call_args.args[0] == wechat_api.CLASSIC_TOKEN_URL


def test_credential_error_does_not_fall_back():
    with (
        patch.object(wechat_api.requests, "post") as post,
        patch.object(wechat_api.requests, "get") as get,
    ):
        post.return_value = FakeResponse(
            {"errcode": 40125, "errmsg": "invalid appsecret"}
        )

        with pytest.raises(ValueError, match="40125"):
            wechat_api.get_access_token("appid", "bad-secret")
        assert get.call_count == 0


def test_classic_error_is_reported_with_stable_detail():
    with (
        patch.object(wechat_api.requests, "post") as post,
        patch.object(wechat_api.requests, "get") as get,
    ):
        post.return_value = FakeResponse({"errcode": -1, "errmsg": "system error"})
        get.return_value = FakeResponse({"errcode": 40164, "errmsg": "invalid ip"})

        with pytest.raises(ValueError) as excinfo:
            wechat_api.get_access_token("appid", "secret")
        message = str(excinfo.value)
        assert "40164" in message
        assert "stable_token returned errcode=-1" in message


def test_force_refresh_bypasses_cache():
    with patch.object(wechat_api.requests, "post") as post:
        post.side_effect = [
            FakeResponse({"access_token": "first", "expires_in": 7200}),
            FakeResponse({"access_token": "second", "expires_in": 7200}),
        ]

        assert wechat_api.get_access_token("appid", "secret") == "first"
        assert (
            wechat_api.get_access_token("appid", "secret", force_refresh=True)
            == "second"
        )
        assert b'"force_refresh": true' in post.call_args.kwargs["data"]
