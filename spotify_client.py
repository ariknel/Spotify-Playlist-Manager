"""Spotify Web API calls: list the user's playlists and delete/unfollow them."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests
import spotipy
from spotipy.exceptions import SpotifyException, SpotifyOauthError

from auth import AuthError

PAGE_SIZE = 50  # max page size of GET /me/playlists
REQUEST_TIMEOUT = 15  # seconds
MAX_RETRIES = 3  # for rate limits, 5xx responses and network hiccups
MAX_RATE_LIMIT_WAIT = 60  # longer Retry-After than this aborts instead of waiting
DEFAULT_RETRY_AFTER = 5
TRANSIENT_STATUSES = (500, 502, 503, 504)


class SpotifyClientError(Exception):
    """A failure with a message that is safe to show to the user."""


class AccessDeniedError(SpotifyClientError):
    """Spotify answered 403 - retrying other playlists will not help."""


class RateLimitError(SpotifyClientError):
    """Spotify is rate limiting us for longer than we are willing to wait."""


class OperationCancelled(Exception):
    """The user cancelled while we were waiting to retry."""


@dataclass(frozen=True)
class Playlist:
    id: str
    uri: str
    name: str
    track_count: Optional[int]  # None when Spotify does not report it
    owner_id: str
    owner_name: str
    is_owned: bool


# on_wait(seconds, reason) is called before sleeping between retries.
WaitCallback = Callable[[int, str], None]


class SpotifyClient:
    def __init__(self, auth_manager) -> None:
        # A plain session (no urllib3 retry adapter) so a 429 reaches us with its
        # Retry-After header intact and we can wait in a way the UI can cancel.
        self._sp = spotipy.Spotify(
            auth_manager=auth_manager,
            requests_session=requests.Session(),
            requests_timeout=REQUEST_TIMEOUT,
        )
        self._user_id: Optional[str] = None

    # ---- public API -------------------------------------------------------

    def get_user(self) -> tuple[str, str]:
        """Return (user_id, display_name) of the logged-in user."""
        me = self._call(self._sp.current_user)
        self._user_id = me.get("id")
        return self._user_id or "", me.get("display_name") or self._user_id or "you"

    def fetch_playlists(
        self, on_progress: Optional[Callable[[int], None]] = None
    ) -> list[Playlist]:
        """Return every playlist in the user's library, following pagination."""
        if self._user_id is None:
            self.get_user()

        playlists: list[Playlist] = []
        offset = 0
        while True:
            page = self._call(
                self._sp.current_user_playlists, limit=PAGE_SIZE, offset=offset
            )
            items = page.get("items") or []
            for raw in items:
                if raw:  # Spotify occasionally returns null entries
                    playlists.append(self._parse(raw))
            if on_progress:
                on_progress(len(playlists))
            # Advance by what we actually got, not by PAGE_SIZE, in case the API
            # ever caps a page below the requested limit.
            offset += len(items)
            if not page.get("next") or not items:
                return playlists

    def unfollow_playlist(
        self,
        playlist: Playlist,
        on_wait: Optional[WaitCallback] = None,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        """Remove a playlist from the user's library.

        For a playlist you own this deletes it; for one you follow it unfollows.
        Spotify's Feb 2026 API update replaced DELETE /playlists/{id}/followers
        with DELETE /me/library taking Spotify URIs (spotipy has no public
        wrapper for playlists yet, hence the private _delete).
        """
        self._call(
            self._sp._delete, "me/library", uris=playlist.uri,
            on_wait=on_wait, cancel=cancel,
        )

    # ---- internals --------------------------------------------------------

    def _parse(self, raw: dict) -> Playlist:
        owner = raw.get("owner") or {}
        owner_id = owner.get("id") or ""
        # Spotify renamed `tracks` to `items` in 2026, and reports contents only
        # for playlists you own or collaborate on, so the count may be absent.
        contents = raw.get("items") or raw.get("tracks") or {}
        total = contents.get("total") if isinstance(contents, dict) else None
        return Playlist(
            id=raw["id"],
            uri=raw.get("uri") or f"spotify:playlist:{raw['id']}",
            name=raw.get("name") or "(untitled)",
            track_count=total if isinstance(total, int) else None,
            owner_id=owner_id,
            owner_name=owner.get("display_name") or owner_id or "unknown",
            is_owned=bool(owner_id) and owner_id == self._user_id,
        )

    def _call(self, fn, *args, on_wait=None, cancel=None, **kwargs):
        """Run an API call, retrying rate limits, 5xx errors and network errors."""
        attempt = 0
        while True:
            try:
                return fn(*args, **kwargs)
            except SpotifyException as exc:
                wait, reason = self._retry_plan(exc, attempt)
                if wait is None:
                    raise self._translate(exc) from exc
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= MAX_RETRIES:
                    raise SpotifyClientError(
                        "Could not reach Spotify. Check your internet connection."
                    ) from exc
                wait, reason = 2 ** attempt, "Network problem"
            except SpotifyOauthError as exc:
                raise AuthError(
                    "Spotify rejected the saved login "
                    f"({exc.error_description or exc.error or exc}). "
                    "Use Log out, then sign in again."
                ) from exc

            attempt += 1
            if on_wait:
                on_wait(wait, reason)
            self._sleep(wait, cancel)

    @staticmethod
    def _retry_plan(exc: SpotifyException, attempt: int):
        """Return (seconds_to_wait, reason), or (None, None) if we should give up."""
        if exc.http_status == 429:
            wait = _retry_after(exc)
            if wait > MAX_RATE_LIMIT_WAIT:
                minutes = max(1, round(wait / 60))
                raise RateLimitError(
                    "Spotify is rate limiting this app and asked us to wait "
                    f"about {minutes} minute(s). Try again later."
                ) from exc
            if attempt >= MAX_RETRIES:
                raise RateLimitError(
                    "Spotify keeps rate limiting this app. Wait a minute and try again."
                ) from exc
            return wait, "Rate limited by Spotify"
        if exc.http_status in TRANSIENT_STATUSES and attempt < MAX_RETRIES:
            return 2 ** attempt, "Spotify had a temporary error"
        return None, None

    @staticmethod
    def _translate(exc: SpotifyException) -> Exception:
        detail = _api_message(exc)
        if exc.http_status == 401:
            return AuthError(
                "Spotify rejected the login token. Use Log out, then sign in again."
            )
        if exc.http_status == 403:
            return AccessDeniedError(
                "Spotify refused the request (403)"
                + (f": {detail}" if detail else "")
                + "\n\nCommon causes:\n"
                "- The Spotify account that owns the app needs an active Premium "
                "subscription (required for apps in Development Mode).\n"
                "- The account you logged in with is not the app owner and is not "
                "listed under User Management in the developer dashboard.\n"
                "- The saved login lacks a required permission - use Log out and "
                "sign in again."
            )
        return SpotifyClientError(
            f"Spotify API error {exc.http_status}" + (f": {detail}" if detail else "")
        )

    @staticmethod
    def _sleep(seconds: float, cancel: Optional[threading.Event]) -> None:
        if cancel is None:
            time.sleep(seconds)
        elif cancel.wait(seconds):
            raise OperationCancelled()


def _retry_after(exc: SpotifyException) -> int:
    try:
        return max(1, int(float(exc.headers.get("Retry-After", DEFAULT_RETRY_AFTER))))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER


def _api_message(exc: SpotifyException) -> str:
    # spotipy formats msg as "<url>:\n <message>"; keep only the message.
    return (exc.msg or "").split("\n", 1)[-1].strip()
