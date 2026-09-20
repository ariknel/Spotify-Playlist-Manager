"""Spotify OAuth2: credentials come from .env, the token is cached on disk."""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from spotipy.cache_handler import CacheFileHandler
from spotipy.exceptions import SpotifyBaseException, SpotifyOauthError
from spotipy.oauth2 import SpotifyOAuth

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CACHE_PATH = BASE_DIR / ".spotify_token_cache"

# Spotify no longer accepts "localhost" as a redirect URI; a loopback IP is required.
# Override with REDIRECT_URI in .env if your app registers something else.
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"

# The first three are the playlist scopes; the last two are what Spotify's
# DELETE /me/library endpoint (used to unfollow playlists) lists as required.
SCOPES = " ".join(
    [
        "playlist-read-private",
        "playlist-modify-private",
        "playlist-modify-public",
        "user-library-modify",
        "user-follow-modify",
    ]
)


class AuthError(Exception):
    """Credentials are missing/invalid or the Spotify login failed."""


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    # Treat the untouched .env.example placeholders as "not set".
    return "" if value.startswith("your_") else value


def build_auth_manager() -> SpotifyOAuth:
    """Create the OAuth manager from .env. Does not talk to Spotify yet."""
    load_dotenv(ENV_PATH)
    client_id = _env("CLIENT_ID")
    client_secret = _env("CLIENT_SECRET")
    if not client_id or not client_secret:
        raise AuthError(
            "CLIENT_ID and/or CLIENT_SECRET are missing.\n\n"
            f"Copy .env.example to .env in\n{BASE_DIR}\n"
            "and paste in the credentials from your Spotify Developer app "
            "(see README.md)."
        )
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=os.getenv("REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI,
        scope=SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(CACHE_PATH)),
        # Only matters for a fresh login: lets you pick/switch accounts.
        show_dialog=True,
        open_browser=True,
    )


def login(auth_manager: SpotifyOAuth) -> None:
    """Make sure a valid token is cached, opening the browser if needed.

    Blocks until the user finishes logging in, so call it off the UI thread.
    """
    try:
        auth_manager.get_access_token(as_dict=False)
    except SpotifyOauthError as exc:
        detail = exc.error_description or exc.error or str(exc)
        hint = ""
        if exc.error == "invalid_client":
            hint = "\n\nCheck CLIENT_ID and CLIENT_SECRET in your .env file."
        elif exc.error == "invalid_grant":
            hint = "\n\nThe saved login has expired or been revoked. Use Log out and sign in again."
        raise AuthError(f"Spotify login failed: {detail}{hint}") from exc
    except SpotifyBaseException as exc:
        raise AuthError(f"Spotify login failed: {exc}") from exc
    except requests.RequestException as exc:
        raise AuthError(f"Could not reach Spotify to log in. Check your internet connection.\n\n{exc}") from exc
    except OSError as exc:
        raise AuthError(
            f"Could not start the local login listener for {auth_manager.redirect_uri}.\n"
            f"Is another program using that port?\n\n{exc}"
        ) from exc


def clear_cached_token() -> None:
    """Forget the saved login so the next start asks for a fresh one."""
    try:
        CACHE_PATH.unlink()
    except FileNotFoundError:
        pass
