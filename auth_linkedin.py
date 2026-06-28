#!/usr/bin/env python3
"""One-time LinkedIn OAuth bootstrap (authorization-code flow).

Run this once on your local Mac to obtain the first LinkedIn access/refresh
token and the author URN, persisted to ``linkedin_token.json`` (gitignored).
``LinkedInPublisher`` then loads and auto-refreshes that token on every publish.

Prerequisites (see README):
  * A LinkedIn Developer app with the ``w_member_social`` product/scope (for
    posting) and ``openid``/``profile`` (the identity scopes that back
    ``/v2/userinfo``) enabled.
  * ``LINKEDIN_CLIENT_ID``, ``LINKEDIN_CLIENT_SECRET`` and
    ``LINKEDIN_REDIRECT_URI`` set in ``.env``. The redirect URI must exactly
    match one registered on the app (default ``http://localhost:8000/callback``).

Flow:
  1. Opens the LinkedIn authorization page in your browser.
  2. Spins up a tiny local HTTP server to catch the redirect with the ``code``.
  3. Exchanges the code for tokens and resolves your author URN.
  4. Writes ``linkedin_token.json``.

Usage:
    python auth_linkedin.py
"""

from __future__ import annotations

import secrets
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from linkedin_post_bot.config import ConfigError, load_config
from linkedin_post_bot.publisher import (
    AUTHORIZE_URL,
    TOKEN_URL,
    USERINFO_URL,
    save_token,
)

# Scopes: posting + OpenID identity (backs /v2/userinfo for the author URN).
SCOPES = "openid profile w_member_social"


class _CallbackHandler(BaseHTTPRequestHandler):
    """Capture the ``code``/``state`` query params from the OAuth redirect."""

    code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.code = (params.get("code") or [None])[0]
        _CallbackHandler.state = (params.get("state") or [None])[0]
        _CallbackHandler.error = (params.get("error") or [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if _CallbackHandler.error:
            msg = f"Authorization failed: {_CallbackHandler.error}"
        else:
            msg = "Authorization received. You can close this tab and return to the terminal."
        self.wfile.write(f"<html><body><p>{msg}</p></body></html>".encode())

    def log_message(self, *args: object) -> None:  # noqa: D102 - silence default logging
        return


def _redirect_host_port(redirect_uri: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8000
    return host, port


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    missing = [
        name
        for name, value in (
            ("LINKEDIN_CLIENT_ID", config.linkedin_client_id),
            ("LINKEDIN_CLIENT_SECRET", config.linkedin_client_secret),
            ("LINKEDIN_REDIRECT_URI", config.linkedin_redirect_uri),
        )
        if not value
    ]
    if missing:
        print(
            "Missing required LinkedIn config: "
            + ", ".join(missing)
            + ". Set them in your .env (see README).",
            file=sys.stderr,
        )
        return 1

    redirect_uri = config.linkedin_redirect_uri
    state = secrets.token_urlsafe(16)

    authorize_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": config.linkedin_client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": SCOPES,
        }
    )

    host, port = _redirect_host_port(redirect_uri)
    print(f"Opening browser for LinkedIn authorization...\n{authorize_url}\n")
    webbrowser.open(authorize_url)

    server = HTTPServer((host, port), _CallbackHandler)
    print(f"Waiting for the OAuth redirect on {host}:{port} ...")
    server.handle_request()  # serve exactly one request (the redirect)

    if _CallbackHandler.error:
        print(f"Authorization failed: {_CallbackHandler.error}", file=sys.stderr)
        return 1
    if not _CallbackHandler.code:
        print("No authorization code received.", file=sys.stderr)
        return 1
    if _CallbackHandler.state != state:
        print("State mismatch - possible CSRF, aborting.", file=sys.stderr)
        return 1

    code = _CallbackHandler.code

    with httpx.Client(timeout=30) as client:
        # Exchange the authorization code for tokens.
        token_resp = client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": config.linkedin_client_id,
                "client_secret": config.linkedin_client_secret,
            },
        )
        token_resp.raise_for_status()
        payload = token_resp.json()

        access_token = payload["access_token"]
        token: dict[str, object] = {"access_token": access_token}
        if payload.get("refresh_token"):
            token["refresh_token"] = payload["refresh_token"]
        if payload.get("expires_in") is not None:
            token["expires_at"] = time.time() + float(payload["expires_in"])

        # Resolve the author URN now so the first publish doesn't have to.
        userinfo = client.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo.raise_for_status()
        member_id = userinfo.json().get("sub")
        if member_id:
            token["author_urn"] = f"urn:li:person:{member_id}"

    save_token("linkedin_token.json", token)
    print("Saved linkedin_token.json. You can now publish from the bot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
