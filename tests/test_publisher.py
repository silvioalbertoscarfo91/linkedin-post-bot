"""Unit tests for LinkedInPublisher (HTTP mocked at the client boundary)."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from linkedin_post_bot.publisher import (
    TOKEN_URL,
    UGC_POSTS_URL,
    USERINFO_URL,
    LinkedInPublisher,
    PublishError,
    TokenError,
    load_token,
    save_token,
)


def _resp(status=200, *, json_body=None, headers=None):
    """Build a fake httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.json.return_value = json_body if json_body is not None else {}
    if status >= 400:
        resp.raise_for_status.side_effect = RuntimeError(f"HTTP {status}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _write_token(path, **overrides):
    token = {
        "access_token": "valid-token",
        "refresh_token": "refresh-token",
        "expires_at": time.time() + 3600,
        "author_urn": "urn:li:person:ABC123",
    }
    token.update(overrides)
    save_token(path, token)
    return token


# --- token persistence round-trip -------------------------------------------


def test_token_persistence_round_trip(tmp_path):
    path = tmp_path / "linkedin_token.json"
    token = {"access_token": "x", "refresh_token": "y", "expires_at": 123.0}
    save_token(path, token)
    assert load_token(path) == token


def test_load_token_missing_raises(tmp_path):
    with pytest.raises(TokenError):
        load_token(tmp_path / "nope.json")


# --- ensure_token ------------------------------------------------------------


def test_ensure_token_returns_valid_token_without_http(tmp_path):
    path = tmp_path / "linkedin_token.json"
    _write_token(path)
    client = MagicMock()

    pub = LinkedInPublisher(client, "cid", "csec", token_path=path)
    assert pub.ensure_token() == "valid-token"
    client.post.assert_not_called()


def test_ensure_token_refreshes_when_expired(tmp_path):
    path = tmp_path / "linkedin_token.json"
    _write_token(path, access_token="old", expires_at=time.time() - 10)
    client = MagicMock()
    client.post.return_value = _resp(
        json_body={"access_token": "fresh", "expires_in": 3600}
    )

    pub = LinkedInPublisher(client, "cid", "csec", token_path=path)
    assert pub.ensure_token() == "fresh"

    # Correct refresh-token grant sent.
    client.post.assert_called_once()
    args, kwargs = client.post.call_args
    assert args[0] == TOKEN_URL
    assert kwargs["data"]["grant_type"] == "refresh_token"
    assert kwargs["data"]["refresh_token"] == "refresh-token"

    # New token persisted.
    saved = json.loads(path.read_text())
    assert saved["access_token"] == "fresh"
    assert saved["expires_at"] > time.time()


def test_ensure_token_no_refresh_token_raises(tmp_path):
    path = tmp_path / "linkedin_token.json"
    _write_token(path, access_token="old", expires_at=time.time() - 10)
    # Drop the refresh token.
    token = load_token(path)
    del token["refresh_token"]
    save_token(path, token)
    client = MagicMock()

    pub = LinkedInPublisher(client, "cid", "csec", token_path=path)
    with pytest.raises(TokenError):
        pub.ensure_token()


def test_refresh_http_error_raises_token_error(tmp_path):
    path = tmp_path / "linkedin_token.json"
    _write_token(path, access_token="old", expires_at=time.time() - 10)
    client = MagicMock()
    client.post.return_value = _resp(status=400)

    pub = LinkedInPublisher(client, "cid", "csec", token_path=path)
    with pytest.raises(TokenError):
        pub.ensure_token()


# --- publish -----------------------------------------------------------------


def test_publish_posts_correct_payload_and_returns_url(tmp_path):
    path = tmp_path / "linkedin_token.json"
    _write_token(path)  # has cached author_urn, valid token
    client = MagicMock()
    client.post.return_value = _resp(
        status=201, headers={"x-restli-id": "urn:li:share:999"}
    )

    pub = LinkedInPublisher(client, "cid", "csec", token_path=path)
    url = pub.publish("Hello LinkedIn")

    assert url == "https://www.linkedin.com/feed/update/urn:li:share:999"

    # Posted to the UGC endpoint with the chosen text and member visibility.
    args, kwargs = client.post.call_args
    assert args[0] == UGC_POSTS_URL
    assert kwargs["headers"]["Authorization"] == "Bearer valid-token"
    body = kwargs["json"]
    assert body["author"] == "urn:li:person:ABC123"
    assert body["lifecycleState"] == "PUBLISHED"
    share = body["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert share["shareCommentary"]["text"] == "Hello LinkedIn"


def test_publish_resolves_author_urn_when_missing(tmp_path):
    path = tmp_path / "linkedin_token.json"
    _write_token(path, author_urn=None)
    client = MagicMock()
    client.get.return_value = _resp(json_body={"sub": "MEMBER42"})
    client.post.return_value = _resp(
        status=201, headers={"x-restli-id": "urn:li:share:1"}
    )

    pub = LinkedInPublisher(client, "cid", "csec", token_path=path)
    pub.publish("text")

    client.get.assert_called_once()
    assert client.get.call_args.args[0] == USERINFO_URL
    body = client.post.call_args.kwargs["json"]
    assert body["author"] == "urn:li:person:MEMBER42"

    # URN persisted for next time.
    assert json.loads(path.read_text())["author_urn"] == "urn:li:person:MEMBER42"


def test_publish_http_error_raises_publish_error_no_false_success(tmp_path):
    path = tmp_path / "linkedin_token.json"
    _write_token(path)
    client = MagicMock()
    client.post.return_value = _resp(status=500)

    pub = LinkedInPublisher(client, "cid", "csec", token_path=path)
    with pytest.raises(PublishError):
        pub.publish("text")


def test_publish_no_post_id_raises(tmp_path):
    path = tmp_path / "linkedin_token.json"
    _write_token(path)
    client = MagicMock()
    client.post.return_value = _resp(status=201, headers={}, json_body={})

    pub = LinkedInPublisher(client, "cid", "csec", token_path=path)
    with pytest.raises(PublishError):
        pub.publish("text")
