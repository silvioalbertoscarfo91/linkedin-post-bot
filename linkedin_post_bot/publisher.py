"""Publish a chosen candidate to the user's real LinkedIn profile.

``LinkedInPublisher`` is the IO module that talks to the LinkedIn REST API. Like
``PostGenerator``, the external boundary (an ``httpx.Client``) is injected so
tests can mock it and stay offline/deterministic. Nothing here knows about
Telegram or the generation API.

Responsibilities:

* ``ensure_token()`` -- load the persisted OAuth token, refreshing it via the
  refresh-token grant when it has expired (or is about to), and persist the
  updated token back to disk.
* ``publish(text) -> url`` -- resolve and cache the author URN, POST a UGC text
  share, and return the public URL of the live post.

Token + author URN live in a local JSON file outside version control (see
``.gitignore``). The one-time authorization-code bootstrap that creates that
file lives in ``auth_linkedin.py`` at the repo root.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_PATH = "linkedin_token.json"

# LinkedIn API endpoints.
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"

# Refresh a little before the real expiry to avoid racing the clock.
EXPIRY_SKEW_SECONDS = 60


class LinkedInError(RuntimeError):
    """Base class for LinkedIn publishing failures."""


class TokenError(LinkedInError):
    """Raised when no usable token exists and it cannot be refreshed.

    The user must re-run the one-time ``auth_linkedin.py`` bootstrap.
    """


class PublishError(LinkedInError):
    """Raised when a post could not be published (no false success)."""


def load_token(path: str | Path) -> dict[str, Any]:
    """Read and parse the persisted token store.

    Raises:
        TokenError: If the file is missing or unreadable. The caller is told to
            run the one-time OAuth bootstrap.
    """
    p = Path(path)
    if not p.exists():
        raise TokenError(
            f"No LinkedIn token found at {p}. Run the one-time OAuth bootstrap: "
            "python auth_linkedin.py"
        )
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise TokenError(f"Could not read LinkedIn token at {p}: {exc}") from exc


def save_token(path: str | Path, token: dict[str, Any]) -> None:
    """Persist the token store atomically-ish (write whole file)."""
    Path(path).write_text(json.dumps(token, indent=2))


class LinkedInPublisher:
    """Publish text posts to the authenticated member's LinkedIn profile."""

    def __init__(
        self,
        client: Any,
        client_id: str,
        client_secret: str,
        *,
        token_path: str | Path = DEFAULT_TOKEN_PATH,
    ) -> None:
        """Create a publisher.

        Args:
            client: An ``httpx.Client``-like object (injected for testability).
            client_id: LinkedIn app client id (for token refresh).
            client_secret: LinkedIn app client secret (for token refresh).
            token_path: Path to the persisted token JSON.
        """
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_path = Path(token_path)
        self._author_urn: str | None = None

    def ensure_token(self) -> str:
        """Return a currently-valid access token, refreshing if needed.

        Loads the persisted token; if it is expired (or within the skew window)
        and a refresh token is available, performs the refresh-token grant and
        persists the new token. Caches the author URN if it was stored.

        Raises:
            TokenError: If no token exists or it cannot be refreshed.
        """
        token = load_token(self._token_path)

        stored_urn = token.get("author_urn")
        if stored_urn and self._author_urn is None:
            self._author_urn = stored_urn

        access_token = token.get("access_token")
        if access_token and not self._is_expired(token):
            return access_token

        # Expired (or no usable access token) -> try to refresh.
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise TokenError(
                "LinkedIn access token has expired and no refresh token is "
                "available. Re-run the one-time OAuth bootstrap: "
                "python auth_linkedin.py"
            )

        return self._refresh(token, refresh_token)

    def _is_expired(self, token: dict[str, Any]) -> bool:
        expires_at = token.get("expires_at")
        if expires_at is None:
            # No expiry recorded -> treat as expired to be safe.
            return True
        return time.time() >= (float(expires_at) - EXPIRY_SKEW_SECONDS)

    def _refresh(self, token: dict[str, Any], refresh_token: str) -> str:
        logger.info("Refreshing LinkedIn access token")
        try:
            response = self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - any failure means re-auth
            raise TokenError(
                f"Failed to refresh LinkedIn access token: {exc}. "
                "Re-run the one-time OAuth bootstrap: python auth_linkedin.py"
            ) from exc

        access_token = payload.get("access_token")
        if not access_token:
            raise TokenError(
                "LinkedIn refresh response did not contain an access_token. "
                "Re-run the one-time OAuth bootstrap: python auth_linkedin.py"
            )

        token["access_token"] = access_token
        expires_in = payload.get("expires_in")
        if expires_in is not None:
            token["expires_at"] = time.time() + float(expires_in)
        # LinkedIn may rotate the refresh token.
        if payload.get("refresh_token"):
            token["refresh_token"] = payload["refresh_token"]
        save_token(self._token_path, token)
        return access_token

    def _resolve_author_urn(self, access_token: str) -> str:
        """Resolve and cache the author URN via the identity endpoint."""
        if self._author_urn:
            return self._author_urn

        try:
            response = self._client.get(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise PublishError(
                f"Could not resolve LinkedIn author identity: {exc}"
            ) from exc

        member_id = payload.get("sub")
        if not member_id:
            raise PublishError(
                "LinkedIn userinfo response did not contain a member id ('sub')."
            )

        self._author_urn = f"urn:li:person:{member_id}"

        # Persist the resolved URN so future runs skip the lookup.
        try:
            stored = load_token(self._token_path)
            stored["author_urn"] = self._author_urn
            save_token(self._token_path, stored)
        except LinkedInError:
            logger.debug("Could not persist author URN; will re-resolve next time")

        return self._author_urn

    def publish(self, text: str) -> str:
        """Publish ``text`` as a UGC share and return the live post URL.

        Raises:
            TokenError: If a valid access token cannot be obtained.
            PublishError: If the post request fails or returns no post id.
        """
        access_token = self.ensure_token()
        author_urn = self._resolve_author_urn(access_token)

        body = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        }

        try:
            response = self._client.post(
                UGC_POSTS_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - never claim a false success
            raise PublishError(f"Failed to publish post to LinkedIn: {exc}") from exc

        post_id = self._extract_post_id(response)
        if not post_id:
            raise PublishError(
                "LinkedIn accepted the request but returned no post id."
            )

        url = f"https://www.linkedin.com/feed/update/{post_id}"
        logger.info("Published LinkedIn post %s", post_id)
        return url

    @staticmethod
    def _extract_post_id(response: Any) -> str | None:
        """Pull the share URN from the response header or body."""
        # LinkedIn returns the new post URN in the ``x-restli-id`` header.
        headers = getattr(response, "headers", None) or {}
        post_id = headers.get("x-restli-id") or headers.get("X-RestLi-Id")
        if post_id:
            return post_id
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return None
        if isinstance(payload, dict):
            return payload.get("id")
        return None
