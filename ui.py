"""customtkinter GUI for Spotify Playlist Manager.

All network work happens on worker threads; they talk to the UI only through
`self._events`, which the main thread drains on a timer (Tk is not thread-safe).
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Optional, Sequence

import customtkinter as ctk

import auth
from auth import AuthError
from spotify_client import (
    AccessDeniedError,
    OperationCancelled,
    Playlist,
    RateLimitError,
    SpotifyClient,
    SpotifyClientError,
)

APP_TITLE = "Spotify Playlist Manager"
POLL_MS = 100
RENDER_CHUNK = 40  # rows built per UI tick, so long libraries don't freeze the window
NAME_MAX_CHARS = 60
OWNER_MAX_CHARS = 22

GREEN = "#1DB954"
GREEN_HOVER = "#17a34a"
RED = "#c0392b"
RED_HOVER = "#e74c3c"
GRAY = "#3a3f4b"
GRAY_HOVER = "#4a505e"
MUTED = "#9aa0a6"
BADGE_OWNED = (GREEN, "#06210f")
BADGE_FOLLOWING = ("#3b4a6b", "#dbe4ff")
STATUS_COLORS = {"info": "#c8c8c8", "ok": GREEN, "warn": "#f0b429", "error": "#ff6b6b"}


def _ellipsize(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plural(n: int, word: str = "playlist") -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


class Dialog(ctk.CTkToplevel):
    """Small modal dialog. `result` is the value of the clicked button, or None."""

    STYLES = {
        "primary": (GREEN, GREEN_HOVER),
        "danger": (RED, RED_HOVER),
        "neutral": (GRAY, GRAY_HOVER),
    }

    def __init__(self, parent, title: str, heading: str, message: str,
                 buttons: Sequence[tuple[str, str, str]], default: Optional[str] = None):
        super().__init__(parent)
        self.result: Optional[str] = None
        self.title(title)
        self.resizable(False, False)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(22, 18))
        ctk.CTkLabel(
            body, text=heading, font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w", justify="left", wraplength=440,
        ).pack(fill="x")
        ctk.CTkLabel(
            body, text=message, anchor="w", justify="left", wraplength=440,
            text_color="#d0d0d0",
        ).pack(fill="x", pady=(10, 18))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        default_button = None
        for label, value, style in reversed(buttons):
            fg, hover = self.STYLES[style]
            button = ctk.CTkButton(
                row, text=label, fg_color=fg, hover_color=hover,
                command=lambda v=value: self._finish(v),
            )
            button.pack(side="right", padx=(8, 0))
            if value == default:
                default_button = button

        self.protocol("WM_DELETE_WINDOW", lambda: self._finish(None))
        self.bind("<Escape>", lambda _e: self._finish(None))

        self.transient(parent)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.wait_visibility()  # grab_set fails on a window that is not yet mapped
        self.grab_set()
        (default_button or self).focus_force()

    def _finish(self, value: Optional[str]) -> None:
        self.result = value
        self.grab_release()
        self.destroy()


def show_dialog(parent, *args, **kwargs) -> Optional[str]:
    dialog = Dialog(parent, *args, **kwargs)
    parent.wait_window(dialog)
    return dialog.result


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")
        self.title(APP_TITLE)
        self.geometry("900x600")
        self.minsize(760, 480)

        self._client: Optional[SpotifyClient] = None
        self._playlists: list[Playlist] = []
        self._vars: dict[str, tk.BooleanVar] = {}
        self._events: queue.Queue = queue.Queue()
        self._busy = False
        self._cancel = threading.Event()
        self._render_generation = 0
        self._pending_note: Optional[tuple[str, str]] = None  # (text, level) shown after a reload

        self._build_ui()
        self._update_controls()
        self.after(POLL_MS, self._drain_events)
        self.after(150, self._start_load)

    # ---- layout -----------------------------------------------------------

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text=APP_TITLE, font=ctk.CTkFont(size=22, weight="bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self._user_label = ctk.CTkLabel(header, text="", text_color=MUTED)
        self._user_label.grid(row=0, column=1, padx=(0, 12))
        self._logout_button = ctk.CTkButton(
            header, text="Log out", width=80, fg_color=GRAY, hover_color=GRAY_HOVER,
            command=self._on_logout,
        )
        self._logout_button.grid(row=0, column=2)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 8))
        toolbar.grid_columnconfigure(3, weight=1)
        self._select_all_button = ctk.CTkButton(
            toolbar, text="Select All", width=100, command=lambda: self._select_all(True))
        self._select_all_button.grid(row=0, column=0, padx=(0, 8))
        self._deselect_all_button = ctk.CTkButton(
            toolbar, text="Deselect All", width=100, fg_color=GRAY, hover_color=GRAY_HOVER,
            command=lambda: self._select_all(False))
        self._deselect_all_button.grid(row=0, column=1, padx=(0, 8))
        self._refresh_button = ctk.CTkButton(
            toolbar, text="Refresh", width=80, fg_color=GRAY, hover_color=GRAY_HOVER,
            command=lambda: self._start_load())
        self._refresh_button.grid(row=0, column=2, padx=(0, 12))
        self._count_label = ctk.CTkLabel(toolbar, text="", text_color=MUTED, anchor="w")
        self._count_label.grid(row=0, column=3, sticky="w")
        self._delete_button = ctk.CTkButton(
            toolbar, text="Delete Selected", width=150, fg_color=RED, hover_color=RED_HOVER,
            command=self._on_delete_clicked)
        self._delete_button.grid(row=0, column=4)

        self._list = ctk.CTkScrollableFrame(self, corner_radius=8)
        self._list.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 8))
        self._list.grid_columnconfigure(0, weight=1)

        status = ctk.CTkFrame(self, corner_radius=0, height=36)
        status.grid(row=3, column=0, sticky="ew")
        status.grid_columnconfigure(0, weight=1)
        self._status_label = ctk.CTkLabel(status, text="", anchor="w", justify="left")
        self._status_label.grid(row=0, column=0, sticky="ew", padx=20, pady=8)
        self._progress = ctk.CTkProgressBar(status, width=180)
        self._progress.grid(row=0, column=1, padx=(0, 12))
        self._progress.grid_remove()
        self._cancel_button = ctk.CTkButton(
            status, text="Cancel", width=80, height=26, fg_color=GRAY, hover_color=GRAY_HOVER,
            command=self._on_cancel)
        self._cancel_button.grid(row=0, column=2, padx=(0, 16))
        self._cancel_button.grid_remove()

    # ---- state helpers ----------------------------------------------------

    def _set_status(self, text: str, level: str = "info") -> None:
        self._status_label.configure(text=text, text_color=STATUS_COLORS[level])

    def _selected(self) -> list[Playlist]:
        return [p for p in self._playlists if self._vars[p.id].get()]

    def _update_controls(self) -> None:
        has_items = bool(self._playlists)
        selected = len(self._selected()) if has_items else 0
        idle = not self._busy
        state = lambda enabled: "normal" if enabled else "disabled"
        self._select_all_button.configure(state=state(idle and has_items))
        self._deselect_all_button.configure(state=state(idle and has_items))
        self._refresh_button.configure(state=state(idle))
        self._logout_button.configure(state=state(idle))
        self._delete_button.configure(
            state=state(idle and selected > 0),
            text=f"Delete Selected ({selected})" if selected else "Delete Selected",
        )
        if has_items:
            owned = sum(p.is_owned for p in self._playlists)
            self._count_label.configure(
                text=f"{selected} of {len(self._playlists)} selected   ·   "
                     f"{owned} owned, {len(self._playlists) - owned} followed")
        else:
            self._count_label.configure(text="")

    def _begin_busy(self, determinate: bool) -> None:
        self._busy = True
        self._progress.grid()
        if determinate:
            self._progress.configure(mode="determinate")
            self._progress.set(0)
            self._cancel_button.grid()
            self._cancel_button.configure(state="normal")
        else:
            self._progress.configure(mode="indeterminate")
            self._progress.start()
        self._update_controls()

    def _end_busy(self) -> None:
        self._busy = False
        self._progress.stop()
        self._progress.grid_remove()
        self._cancel_button.grid_remove()
        self._update_controls()

    # ---- playlist list ----------------------------------------------------

    def _set_playlists(self, playlists: list[Playlist]) -> None:
        self._playlists = playlists
        self._vars = {p.id: tk.BooleanVar(value=False) for p in playlists}
        self._render_generation += 1
        for widget in self._list.winfo_children():
            widget.destroy()
        if playlists:
            self._render_rows(self._render_generation, 0)
        else:
            ctk.CTkLabel(self._list, text="No playlists found.", text_color=MUTED).grid(
                row=0, column=0, pady=40)
        self._update_controls()

    def _render_rows(self, generation: int, start: int) -> None:
        if generation != self._render_generation:  # a newer list replaced this one
            return
        for row, playlist in enumerate(self._playlists[start:start + RENDER_CHUNK], start):
            self._add_row(row, playlist)
        if start + RENDER_CHUNK < len(self._playlists):
            self.after(1, self._render_rows, generation, start + RENDER_CHUNK)

    def _add_row(self, row: int, playlist: Playlist) -> None:
        pad = {"pady": 3}
        ctk.CTkCheckBox(
            self._list, text=_ellipsize(playlist.name, NAME_MAX_CHARS),
            variable=self._vars[playlist.id], command=self._update_controls,
            fg_color=GREEN, hover_color=GREEN_HOVER, checkbox_width=20, checkbox_height=20,
        ).grid(row=row, column=0, sticky="w", padx=(8, 8), **pad)

        badge_bg, badge_fg = BADGE_OWNED if playlist.is_owned else BADGE_FOLLOWING
        ctk.CTkLabel(
            self._list, text="Owned" if playlist.is_owned else "Following", width=78,
            height=22, corner_radius=6, fg_color=badge_bg, text_color=badge_fg,
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=row, column=1, padx=8, **pad)

        ctk.CTkLabel(
            self._list, text="" if playlist.is_owned else
            f"by {_ellipsize(playlist.owner_name, OWNER_MAX_CHARS)}",
            text_color=MUTED, anchor="w", width=150,
        ).grid(row=row, column=2, sticky="w", padx=8, **pad)

        count = playlist.track_count
        ctk.CTkLabel(
            self._list, text="—" if count is None else _plural(count, "track"),
            text_color=MUTED, anchor="e", width=80,
        ).grid(row=row, column=3, sticky="e", padx=(8, 12), **pad)

    def _select_all(self, value: bool) -> None:
        for var in self._vars.values():
            var.set(value)
        self._update_controls()

    # ---- loading (login + fetch) -----------------------------------------

    def _start_load(self, note: Optional[tuple[str, str]] = None) -> None:
        if self._busy:
            return
        self._pending_note = note
        self._begin_busy(determinate=False)
        if self._client is None:
            self._set_status("Connecting to Spotify… a browser window may open for you to log in.")
        else:
            self._set_status("Loading playlists…")
        threading.Thread(target=self._load_worker, args=(self._client,), daemon=True).start()

    def _load_worker(self, client: Optional[SpotifyClient]) -> None:
        try:
            if client is None:
                manager = auth.build_auth_manager()
                auth.login(manager)
                client = SpotifyClient(manager)
            _user_id, user_name = client.get_user()
            playlists = client.fetch_playlists(
                on_progress=lambda n: self._events.put(("status", f"Loading playlists… {n} so far", "info")))
            self._events.put(("loaded", client, user_name, playlists))
        except (AuthError, SpotifyClientError) as exc:
            self._events.put(("load_failed", str(exc)))
        except Exception as exc:  # last resort: never leave the UI stuck in "busy"
            self._events.put(("load_failed", f"Unexpected error: {exc!r}"))

    def _on_loaded(self, client: SpotifyClient, user_name: str, playlists: list[Playlist]) -> None:
        self._client = client
        self._user_label.configure(text=f"Logged in as {user_name}")
        self._refresh_button.configure(text="Refresh")
        self._set_playlists(playlists)
        self._end_busy()
        owned = sum(p.is_owned for p in playlists)
        summary = f"{_plural(len(playlists))} loaded ({owned} owned, {len(playlists) - owned} followed)."
        if self._pending_note:
            text, level = self._pending_note
            self._set_status(f"{text} {_plural(len(playlists))} left.", level)
        else:
            self._set_status(summary)
        self._pending_note = None

    def _on_load_failed(self, message: str) -> None:
        self._end_busy()
        self._pending_note = None
        self._set_status("Could not connect to Spotify. Click Refresh to try again.", "error")
        show_dialog(
            self, "Spotify error", "Could not load your playlists", message,
            [("OK", "ok", "primary")])

    def _on_logout(self) -> None:
        auth.clear_cached_token()
        self._client = None
        self._user_label.configure(text="")
        self._refresh_button.configure(text="Log in")
        self._set_playlists([])
        self._set_status("Logged out. Click Log in to sign in again.")

    # ---- deleting ---------------------------------------------------------

    def _on_delete_clicked(self) -> None:
        targets = self._selected()
        if not targets or self._busy or self._client is None:
            return
        owned = sum(p.is_owned for p in targets)
        followed = len(targets) - owned
        lines = []
        if owned:
            lines.append(f"• {_plural(owned)} you own — removed from your library.")
        if followed:
            lines.append(f"• {_plural(followed)} you follow — you will just unfollow them.")
        lines.append(
            "\nOwned playlists can usually be restored for a while from the "
            "“Recover playlists” page of your Spotify account.")
        choice = show_dialog(
            self, "Confirm deletion", f"Delete {_plural(len(targets))}?", "\n".join(lines),
            [("Cancel", "cancel", "neutral"), (f"Delete {len(targets)}", "delete", "danger")],
            default="cancel")
        if choice != "delete":
            return

        self._cancel.clear()
        self._begin_busy(determinate=True)
        self._set_status(f"Deleting 0/{len(targets)}…")
        threading.Thread(
            target=self._delete_worker, args=(self._client, targets), daemon=True).start()

    def _delete_worker(self, client: SpotifyClient, targets: list[Playlist]) -> None:
        put = self._events.put
        total = len(targets)
        deleted = 0
        failures: list[tuple[str, str]] = []
        aborted: Optional[tuple[str, str]] = None  # (playlist name, reason)
        cancelled = False
        for index, playlist in enumerate(targets, 1):
            if self._cancel.is_set():
                cancelled = True
                break
            put(("progress", index, total, playlist.name))
            try:
                client.unfollow_playlist(
                    playlist, cancel=self._cancel,
                    on_wait=lambda secs, why: put(("waiting", secs, why)))
                deleted += 1
            except OperationCancelled:
                cancelled = True
                break
            except (AuthError, RateLimitError, AccessDeniedError) as exc:
                aborted = (playlist.name, str(exc))  # every remaining call would fail too
                break
            except SpotifyClientError as exc:
                failures.append((playlist.name, str(exc)))
            except Exception as exc:
                failures.append((playlist.name, f"Unexpected error: {exc!r}"))
        put(("delete_done", deleted, total, failures, aborted, cancelled))

    def _on_status(self, text: str, level: str) -> None:
        self._set_status(text, level)

    def _on_progress(self, index: int, total: int, name: str) -> None:
        self._set_status(f"Deleting {index}/{total}: {_ellipsize(name, 50)}")
        self._progress.set((index - 1) / total)

    def _on_waiting(self, seconds: int, reason: str) -> None:
        self._set_status(f"{reason}. Retrying in {seconds}s…", "warn")

    def _on_cancel(self) -> None:
        self._cancel.set()
        self._cancel_button.configure(state="disabled")
        self._set_status("Cancelling…", "warn")

    def _on_delete_done(self, deleted: int, total: int, failures: list[tuple[str, str]],
                        aborted: Optional[tuple[str, str]], cancelled: bool) -> None:
        self._end_busy()
        if cancelled:
            summary, level = f"Cancelled: deleted {deleted} of {total}.", "warn"
        elif failures or aborted:
            summary, level = f"Deleted {deleted} of {total}; some could not be deleted.", "warn"
        else:
            summary, level = f"Deleted {_plural(deleted)}.", "ok"

        if failures or aborted:
            parts = []
            if aborted:
                parts.append(f"Stopped at “{_ellipsize(aborted[0], 50)}”:\n{aborted[1]}")
            if failures:
                shown = [f"• {_ellipsize(n, 40)}: {m}" for n, m in failures[:8]]
                if len(failures) > 8:
                    shown.append(f"…and {len(failures) - 8} more")
                parts.append("Failed:\n" + "\n".join(shown))
            show_dialog(
                self, "Deletion incomplete", f"Deleted {deleted} of {total} playlists",
                "\n\n".join(parts), [("OK", "ok", "primary")])

        self._start_load(note=(summary, level))  # refresh the list

    # ---- worker -> UI events ---------------------------------------------

    def _drain_events(self) -> None:
        self.after(POLL_MS, self._drain_events)
        while True:
            try:
                name, *args = self._events.get_nowait()
            except queue.Empty:
                return
            handler = getattr(self, f"_on_{name}", None)
            if handler:
                handler(*args)
