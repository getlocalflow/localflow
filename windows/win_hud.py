"""Floating pill HUD (tkinter). Bottom-center, frameless, always on top.
States: waveform (recording) / spinner (processing) / check (done) / error.
All methods must be called on the tk main thread (daemon marshals via
root.after). Auto-hides 1.6s after done/error."""
import logging
import math
import tkinter as tk

log = logging.getLogger("localflow.hud")

W, H, PAD_BOTTOM = 260, 48, 60
BG, FG = "#1e1e1e", "#e8e8e8"
BAR_N = 21


class WinHUD:
    def __init__(self, root):
        self.root = root
        self.on_click = None
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.94)
        except tk.TclError:
            log.info("alpha unsupported on this Tk build")
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        self.win.geometry(f"{W}x{H}+{(sw - W) // 2}+{sh - H - PAD_BOTTOM}")
        self.canvas = tk.Canvas(self.win, width=W, height=H, bg=BG,
                                highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._clicked)
        self.state = "hidden"
        self._level = 0.0
        self._spin = 0
        self._anim_job = None
        self._hide_job = None
        self.win.withdraw()

    def _clicked(self, _event):
        if self.on_click:
            self.on_click()

    # ---- state entries (tk main thread only) ----
    def show_recording(self):
        self._cancel_jobs()
        self.state = "recording"
        self._timer_text = ""
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self._animate()

    def set_level(self, rms):
        self._level = max(0.0, min(1.0, rms * 12))

    def set_timer(self, seconds, warn):
        seconds = max(0, seconds)
        m, s = divmod(int(seconds), 60)
        self._timer_text = f"{m}:{s:02d}"
        self._timer_warn = warn

    def show_processing(self):
        self._cancel_jobs()
        self.state = "processing"
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self._animate()

    def show_done(self, word_count):
        self._cancel_jobs()
        self.state = "done"
        self._draw_static(f"✓  {word_count} words")
        self._hide_job = self.root.after(1600, self.hide)

    def show_error(self, message):
        self._cancel_jobs()
        self.state = "error"
        self._draw_static(message[:36] + ("..." if len(message) > 36 else ""),
                          fg="#ff8a8a")
        self._hide_job = self.root.after(2600, self.hide)

    def hide(self):
        self._cancel_jobs()
        self.state = "hidden"
        self.win.withdraw()

    # ---- drawing ----
    def _cancel_jobs(self):
        for job in (self._anim_job, self._hide_job):
            if job:
                self.root.after_cancel(job)
        self._anim_job = self._hide_job = None

    def _draw_static(self, text, fg=FG):
        self.canvas.delete("all")
        self._rounded_bg()
        self.canvas.create_text(W // 2, H // 2, text=text, fill=fg,
                                font=("Segoe UI", 12, "bold"))
        self.win.deiconify()
        self.win.attributes("-topmost", True)

    def _rounded_bg(self):
        r = H // 2
        self.canvas.create_oval(0, 0, H, H, fill=BG, outline=BG)
        self.canvas.create_oval(W - H, 0, W, H, fill=BG, outline=BG)
        self.canvas.create_rectangle(r, 0, W - r, H, fill=BG, outline=BG)

    def _animate(self):
        if self.state not in ("recording", "processing"):
            return
        self.canvas.delete("all")
        self._rounded_bg()
        if self.state == "recording":
            cx0 = 28
            for i in range(BAR_N):
                phase = (self._spin + i) * 0.55
                amp = 4 + (H * 0.32) * self._level * abs(math.sin(phase))
                x = cx0 + i * ((W - 76) / BAR_N)
                self.canvas.create_line(x, H / 2 - amp, x, H / 2 + amp,
                                        fill="#7ec8ff", width=3,
                                        capstyle=tk.ROUND)
            t = getattr(self, "_timer_text", "")
            if t:
                color = "#ffcf7e" if getattr(self, "_timer_warn", False) else "#9a9a9a"
                self.canvas.create_text(W - 26, H // 2, text=t, fill=color,
                                        font=("Segoe UI", 9))
        else:  # processing spinner
            for i in range(8):
                a = (self._spin * 0.35) + i * (3.14159 / 4)
                x = W / 2 + 14 * math.cos(a)
                y = H / 2 + 14 * math.sin(a)
                shade = 90 + (i * 18) % 160
                self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3,
                                        fill=f"#{shade:02x}{shade:02x}{shade:02x}",
                                        outline="")
        self._spin += 1
        self._anim_job = self.root.after(66, self._animate)
