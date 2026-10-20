"""Color palette, fonts and shared widgets in a dark style."""
import tkinter as tk

BG = "#0d0f14"
SURFACE = "#161922"
SURFACE_2 = "#1f2331"
BORDER = "#2b3144"
TEXT = "#e7e9f0"
MUTED = "#7d8497"
ACCENT = "#8b6cff"
ACCENT_HOVER = "#a18bff"
WARM = "#ffb454"

FONT = ("Helvetica", 10)
FONT_BOLD = ("Helvetica", 10, "bold")
FONT_SMALL = ("Helvetica", 9)
FONT_TITLE = ("Helvetica", 16, "bold")
FONT_SECTION = ("Helvetica", 8, "bold")
FONT_BIG = ("Helvetica", 24, "bold")


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class Button(tk.Label):
    """Flat button based on Label. On macOS tk.Button ignores colors, hence this version."""

    def __init__(self, master, text, command, accent=False):
        self._command = command
        self._accent = accent
        self._base = ACCENT if accent else SURFACE_2
        super().__init__(master, text=text, bg=self._base, fg=TEXT,
                         font=FONT_BOLD if accent else FONT,
                         padx=12, pady=7, cursor="hand2")
        self.bind("<Enter>", lambda e: self.configure(bg=ACCENT_HOVER if accent else BORDER))
        self.bind("<Leave>", lambda e: self.configure(bg=self._base))
        self.bind("<Button-1>", lambda e: self._command())

    def select(self, on):
        """Highlights the button as active (e.g. the selected view mode)."""
        self._base = ACCENT if on else SURFACE_2
        self.configure(bg=self._base)

    def set_text(self, text):
        self.configure(text=text)


def section(master, title):
    tk.Label(master, text=title, bg=SURFACE, fg=MUTED, font=FONT_SECTION,
             anchor="w").pack(fill="x", padx=20, pady=(18, 6))


def make_slider(master, lo, hi, value, length=250):
    s = tk.Scale(master, from_=lo, to=hi, orient="horizontal", resolution=1,
                 showvalue=False, length=length, bg=SURFACE, troughcolor=SURFACE_2,
                 activebackground=ACCENT, highlightthickness=0, bd=0,
                 relief="flat", sliderlength=16, width=10)
    s.set(value)  # set the value before attaching the command
    return s
