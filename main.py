"""Entry point for Spotify Playlist Manager."""

import logging

from ui import App


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    App().mainloop()


if __name__ == "__main__":
    main()
