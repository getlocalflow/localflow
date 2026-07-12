"""System tray icon + menu (pystray). Runs detached; menu callbacks hop to
the tk main thread via daemon.ui() so tray never touches tk directly."""
import logging

log = logging.getLogger("localflow.tray")

STATE_COLOR = {"idle": (126, 200, 255), "recording": (255, 92, 92),
               "processing": (255, 176, 66), "attention": (255, 207, 126)}


def _dot_icon(rgb):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(30, 30, 34, 255))
    d.ellipse((18, 18, 46, 46), fill=rgb + (255,))
    return img


class Tray:
    def __init__(self, daemon, on_quit):
        self.daemon = daemon
        self.on_quit = on_quit
        self.icon = None

    def _menu(self):
        import pystray
        d = self.daemon
        # NOTE: pystray invokes actions with (icon, item); *a absorbs them.
        return pystray.Menu(
            pystray.MenuItem(lambda item: d.status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause Listening", lambda *a: d.ui(d.toggle_pause),
                             checked=lambda item: d.paused),
            pystray.MenuItem("Copy Last Transcript", lambda *a: d.ui(d.copy_last)),
            pystray.MenuItem("Retry Last Recording", lambda *a: d.ui(d.retry_last)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Raw Mode by Default",
                             lambda *a: d.ui(d.toggle_raw_default),
                             checked=lambda item: d.raw_by_default),
            pystray.MenuItem("Sounds", lambda *a: d.ui(d.toggle_sounds),
                             checked=lambda item: d.sounds_on),
            pystray.MenuItem("Dictionary...", lambda *a: d.ui(d.open_dictionary)),
            pystray.MenuItem("Replacements...", lambda *a: d.ui(d.open_replacements)),
            pystray.MenuItem("Open History Folder", lambda *a: d.ui(d.open_history)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit LocalFlow", lambda *a: self.on_quit()),
        )

    def start(self):
        import pystray
        self.icon = pystray.Icon("LocalFlow", _dot_icon(STATE_COLOR["idle"]),
                                 "LocalFlow", self._menu())
        self.icon.run_detached()

    def set_state(self, state):
        if self.icon:
            self.icon.icon = _dot_icon(STATE_COLOR.get(state, STATE_COLOR["idle"]))

    def stop(self):
        if self.icon:
            self.icon.stop()
