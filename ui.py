"""Application window: control panels, preview, mask, animation and step history."""
import math
import os
import tkinter as tk
from collections import namedtuple
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageOps, ImageTk

import mask as maskmod
import sorter
from theme import *

WORK_MAX = 640   # side of the working copy (px); the preview and the animation run on it
TICK_MS = 33     # ~30 frames per second
PANEL_W = 330    # width of each side panel (px)

ANIMATION, RESULT, MOTION, MASK, AFFECTED = "Animation", "Result", "Motion map", "Mask", "Affected"
MODES = (ANIMATION, RESULT, MOTION, MASK, AFFECTED)
IMG_TYPES = [("Images", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"), ("All files", "*.*")]
MASK_OPTIONS = (("protect", "Protect selection"), ("sort", "Sort selection only"))

# A committed pass: settings, the mask at full resolution and the property threshold (lo, hi in 0..1).
# The threshold is evaluated when the pass runs, on the image of that moment, so only lo and hi are stored.
Spec = namedtuple("Spec", "angle prop levels reverse algo region lo hi")


def _make_palette():
    """Motion map palette: background -> violet -> warm orange -> white."""
    stops = [hex_to_rgb(BG), hex_to_rgb(ACCENT), hex_to_rgb(WARM), (255, 255, 255)]
    pos = [0.0, 0.4, 0.75, 1.0]
    xs = np.linspace(0, 1, 256)
    lut = np.zeros((256, 3), np.uint8)
    for c in range(3):
        lut[:, c] = np.interp(xs, pos, [s[c] for s in stops])
    return lut


PALETTE = _make_palette()


def _label(options, key):
    return dict(options)[key]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Pixel Sort Studio")
        self.geometry("1400x860")
        self.minsize(1180, 700)
        self.configure(bg=BG)

        self.src = None           # original, full resolution (PIL RGB)
        self.start = None         # original at working resolution (np.uint8)
        self.base = None          # committed state, working resolution (np.uint8)
        self.chain = []           # committed passes (Spec), starting from the original
        self.history = []         # earlier (base, chain) pairs, for undo
        self.full_cache = None    # full-resolution result, computed lazily
        self.last_heat = None     # motion map of the last pass
        self.mask = None          # PIL 'L', full resolution; None = no selection
        self.protect = True       # True: the selection is protected; False: only the selection is sorted
        self.angle = 0.0
        self.prop = "luma"
        self.algo = "odd_even"
        self.levels = None        # None = full precision
        self.thr_lo = 0           # threshold: lower bound of the sorted property (%)
        self.thr_hi = 100         # threshold: upper bound (%)
        self.reverse = tk.BooleanVar(value=False)
        self.brush = 60
        self.speed = 2

        self.run = None           # active pass (sorter.Animator)
        self.run_spec = None      # settings of the active pass
        self.running = False
        self.mode = ANIMATION
        self._panels = []         # (widget path, canvas) of the scrollable side panels
        self._after_id = None
        self._photo = None
        self._disp = None
        self._origin = (0, 0)
        self._scale = 1.0
        self._painting = False
        self._paint_value = 255
        self._last = None

        self._build_ui()
        self._bind_keys()
        self._draw_compass()
        self._refresh_chain()
        self._draw()

    # ---------- building the interface ----------

    def _make_panel(self, side):
        """A scrollable side panel packed to `side`. Returns the frame to put widgets in."""
        outer = tk.Frame(self, bg=SURFACE, width=PANEL_W)
        outer.pack(side=side, fill="y")
        outer.pack_propagate(False)

        canvas = tk.Canvas(outer, bg=SURFACE, highlightthickness=0, width=PANEL_W - 16)
        sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=SURFACE)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        self._panels.append((str(outer), canvas))
        return inner

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Pix.Horizontal.TProgressbar", troughcolor=SURFACE_2, background=ACCENT,
                        bordercolor=SURFACE_2, lightcolor=ACCENT, darkcolor=ACCENT)

        # Both panels are created before the main area, so the main area gets only what is left.
        left = self._make_panel("left")
        right = self._make_panel("right")

        self.bind_all("<MouseWheel>", self._on_wheel)                  # Windows and macOS
        if self.tk.call("tk", "windowingsystem") == "x11":             # Linux: the wheel sends buttons 4 and 5
            self.bind_all("<Button-4>", lambda e: self._on_wheel(e, 1))
            self.bind_all("<Button-5>", lambda e: self._on_wheel(e, -1))

        # ----- left panel: what and how to sort -----
        tk.Label(left, text="Pixel Sort", bg=SURFACE, fg=TEXT, font=FONT_TITLE).pack(anchor="w", padx=20, pady=(22, 0))
        tk.Label(left, text="sort pixels along a chosen direction", bg=SURFACE, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w", padx=20)

        # IMAGE
        section(left, "IMAGE")
        Button(left, "Choose image…", self.open_image).pack(fill="x", padx=20)
        self.lbl_file = tk.Label(left, text="no file", bg=SURFACE, fg=MUTED,
                                 font=FONT_SMALL, anchor="w", justify="left")
        self.lbl_file.pack(fill="x", padx=20, pady=(8, 0))

        # DIRECTION
        section(left, "DIRECTION")
        top = tk.Frame(left, bg=SURFACE)
        top.pack(fill="x", padx=20)
        self.compass = tk.Canvas(top, width=96, height=96, bg=SURFACE, highlightthickness=0)
        self.compass.pack(side="left")
        info = tk.Frame(top, bg=SURFACE)
        info.pack(side="left", padx=(14, 0), fill="both", expand=True)
        self.lbl_angle = tk.Label(info, text="0°", bg=SURFACE, fg=TEXT, font=FONT_BIG)
        self.lbl_angle.pack(anchor="w")
        tk.Label(info, text="0° → right\n90° → up", bg=SURFACE, fg=MUTED,
                 font=FONT_SMALL, justify="left").pack(anchor="w")
        self.scale_angle = make_slider(left, 0, 359, 0)
        self.scale_angle.pack(fill="x", padx=20, pady=(12, 0))
        self.scale_angle.configure(command=self._on_angle)
        grid = tk.Frame(left, bg=SURFACE)
        grid.pack(fill="x", padx=20, pady=(10, 0))
        for i, a in enumerate(range(0, 360, 45)):
            Button(grid, f"{a}°", lambda a=a: self.set_angle(a)).grid(
                row=i // 4, column=i % 4, padx=2, pady=2, sticky="ew")
        for c in range(4):
            grid.columnconfigure(c, weight=1)

        # SORT BY / ALGORITHM
        section(left, "SORT BY")
        self.prop_buttons = self._choice_grid(left, sorter.PROPERTIES, self.prop, self.set_prop, cols=2)
        section(left, "ALGORITHM")
        self.algo_buttons = self._choice_grid(left, sorter.ALGORITHMS, self.algo, self.set_algo, cols=1)
        tk.Label(left, text="Bubble: one pass per frame. Slowest, but the most spectacular.",
                 bg=SURFACE, fg=MUTED, font=FONT_SMALL, justify="left", wraplength=270).pack(anchor="w", padx=20, pady=(6, 0))

        # PARAMETERS
        section(left, "PARAMETERS")
        lv = tk.Frame(left, bg=SURFACE)
        lv.pack(fill="x", padx=20)
        tk.Label(lv, text="Key quantization", bg=SURFACE, fg=TEXT, font=FONT).pack(side="left")
        self.lbl_levels = tk.Label(lv, text="full", bg=SURFACE, fg=MUTED, font=FONT_SMALL)
        self.lbl_levels.pack(side="right")
        self.scale_levels = make_slider(left, 8, 256, 256)
        self.scale_levels.pack(fill="x", padx=20)
        self.scale_levels.configure(command=self._on_levels)
        tk.Label(left, text="Fewer levels = coarser sort. Pixels in the same level keep their original order.",
                 bg=SURFACE, fg=MUTED, font=FONT_SMALL, justify="left", wraplength=270).pack(anchor="w", padx=20)
        tk.Checkbutton(left, text="Descending", variable=self.reverse, command=self._on_setting_change,
                       bg=SURFACE, fg=TEXT, selectcolor=SURFACE_2, activebackground=SURFACE,
                       activeforeground=TEXT, highlightthickness=0, font=FONT).pack(anchor="w", padx=20, pady=(6, 0))

        # THRESHOLD
        section(left, "THRESHOLD")
        tr = tk.Frame(left, bg=SURFACE)
        tr.pack(fill="x", padx=20)
        tk.Label(tr, text="Affected pixel range", bg=SURFACE, fg=TEXT, font=FONT).pack(side="left")
        self.lbl_thr = tk.Label(tr, text="0–100%", bg=SURFACE, fg=MUTED, font=FONT_SMALL)
        self.lbl_thr.pack(side="right")
        self.scale_thr_lo = make_slider(left, 0, 100, self.thr_lo)
        self.scale_thr_lo.pack(fill="x", padx=20)
        self.scale_thr_lo.configure(command=self._on_thr_lo)
        self.scale_thr_hi = make_slider(left, 0, 100, self.thr_hi)
        self.scale_thr_hi.pack(fill="x", padx=20)
        self.scale_thr_hi.configure(command=self._on_thr_hi)
        tk.Label(left, text="Top slider: lower bound, bottom slider: upper bound, both on the property chosen in "
                            "“Sort by”. Pixels outside the range stay in place and split the line into separate "
                            "segments. Preview: the Affected view.",
                 bg=SURFACE, fg=MUTED, font=FONT_SMALL, justify="left", wraplength=270).pack(anchor="w", padx=20, pady=(6, 24))

        # ----- right panel: mask, animation, export -----
        # MASK
        section(right, "MASK")
        bp = tk.Frame(right, bg=SURFACE)
        bp.pack(fill="x", padx=20)
        tk.Label(bp, text="Brush size", bg=SURFACE, fg=TEXT, font=FONT).pack(side="left")
        self.lbl_brush = tk.Label(bp, text=f"{self.brush} px", bg=SURFACE, fg=MUTED, font=FONT_SMALL)
        self.lbl_brush.pack(side="right")
        self.scale_brush = make_slider(right, 2, 200, self.brush)
        self.scale_brush.pack(fill="x", padx=20)
        self.scale_brush.configure(command=self._on_brush)
        self.mask_buttons = self._choice_grid(right, MASK_OPTIONS, "protect" if self.protect else "sort",
                                              self.set_mask_mode, cols=1)
        mb = tk.Frame(right, bg=SURFACE)
        mb.pack(fill="x", padx=20, pady=(6, 0))
        Button(mb, "Clear", self.clear_mask).pack(side="left", fill="x", expand=True)
        Button(mb, "Invert", self.invert_mask).pack(side="left", fill="x", expand=True, padx=(6, 0))
        tk.Label(right, text="Paint in the Mask view (M). LMB paints, RMB or Shift+LMB erases.",
                 bg=SURFACE, fg=MUTED, font=FONT_SMALL, justify="left", wraplength=270).pack(anchor="w", padx=20, pady=(6, 0))

        # ANIMATION
        section(right, "ANIMATION")
        sp = tk.Frame(right, bg=SURFACE)
        sp.pack(fill="x", padx=20)
        tk.Label(sp, text="Speed", bg=SURFACE, fg=TEXT, font=FONT).pack(side="left")
        self.lbl_speed = tk.Label(sp, text=f"{self.speed}×", bg=SURFACE, fg=MUTED, font=FONT_SMALL)
        self.lbl_speed.pack(side="right")
        self.scale_speed = make_slider(right, 1, 40, self.speed)
        self.scale_speed.pack(fill="x", padx=20)
        self.scale_speed.configure(command=self._on_speed)
        btns = tk.Frame(right, bg=SURFACE)
        btns.pack(fill="x", padx=20, pady=(12, 0))
        self.btn_play = Button(btns, "▶  Play", self.toggle_play, accent=True)
        self.btn_play.pack(fill="x")
        self.progress = ttk.Progressbar(right, style="Pix.Horizontal.TProgressbar", maximum=100)
        self.progress.pack(fill="x", padx=20, pady=(12, 0))
        hist = tk.Frame(right, bg=SURFACE)
        hist.pack(fill="x", padx=20, pady=(10, 0))
        Button(hist, "↶ Undo", self.undo).pack(side="left", fill="x", expand=True)
        Button(hist, "↺ Original", self.reset_to_original).pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.lbl_chain = tk.Label(right, text="", bg=SURFACE, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.lbl_chain.pack(fill="x", padx=20, pady=(6, 0))

        # EXPORT
        section(right, "EXPORT")
        ex = tk.Frame(right, bg=SURFACE)
        ex.pack(fill="x", padx=20, pady=(0, 24))
        Button(ex, "Save result", self.save_result).pack(side="left", fill="x", expand=True)
        Button(ex, "Save GIF", self.save_gif).pack(side="left", fill="x", expand=True, padx=(6, 0))

        # ----- main area -----
        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        modebar = tk.Frame(main, bg=BG)
        modebar.pack(fill="x", padx=24, pady=(18, 0))
        self.mode_buttons = {}
        for m in MODES:
            b = Button(modebar, m, lambda m=m: self.set_mode(m))
            b.pack(side="left", padx=(0, 6))
            self.mode_buttons[m] = b
        self.stage = tk.Canvas(main, bg=BG, highlightthickness=0)
        self.stage.pack(fill="both", expand=True, padx=24, pady=16)
        self._bind_stage()
        self.lbl_status = tk.Label(main, text="", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.lbl_status.pack(fill="x", padx=24, pady=(0, 12))

    def _choice_grid(self, parent, options, current, command, cols):
        wrap = tk.Frame(parent, bg=SURFACE)
        wrap.pack(fill="x", padx=20)
        buttons = {}
        for i, (key, label) in enumerate(options):
            b = Button(wrap, label, lambda k=key: command(k))
            b.grid(row=i // cols, column=i % cols, sticky="ew", padx=2, pady=2)
            buttons[key] = b
        for c in range(cols):
            wrap.columnconfigure(c, weight=1)
        self._mark(buttons, current)
        return buttons

    @staticmethod
    def _mark(buttons, current):
        for key, b in buttons.items():
            b.select(key == current)

    def _bind_stage(self):
        s = self.stage
        s.bind("<Configure>", lambda e: self._draw())
        s.bind("<ButtonPress-1>", lambda e: self._paint_start(e, add=not (e.state & 0x0001)))
        s.bind("<B1-Motion>", self._paint_move)
        s.bind("<ButtonRelease-1>", self._paint_end)
        for b in ("2", "3"):   # right button: 2 on macOS, 3 on Windows and Linux
            s.bind(f"<ButtonPress-{b}>", lambda e: self._paint_start(e, add=False))
            s.bind(f"<B{b}-Motion>", self._paint_move)
            s.bind(f"<ButtonRelease-{b}>", self._paint_end)
        s.bind("<Motion>", lambda e: self._draw_cursor(e.x, e.y))
        s.bind("<Leave>", lambda e: s.delete("cursor"))

    def _bind_keys(self):
        self.bind("<space>", lambda e: self.toggle_play())
        self.bind("<o>", lambda e: self.open_image())
        self.bind("<m>", lambda e: self.set_mode(MASK))
        self.bind("<r>", lambda e: self.reset_to_original())
        self.bind("<Control-z>", lambda e: self.undo())

    def _on_wheel(self, e, step=None):
        """Scrolls the side panel under the pointer. step: +1 up, -1 down (Linux buttons 4 and 5)."""
        if step is None:                       # Windows and macOS: the direction is the sign of delta
            step = 1 if e.delta > 0 else -1
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except KeyError:
            return
        if w is None:
            return
        path = str(w)
        for outer, canvas in self._panels:
            if path == outer or path.startswith(outer + "."):
                canvas.yview_scroll(-step, "units")
                return

    # ---------- settings ----------

    def _on_angle(self, v):
        self.set_angle(int(float(v)))

    def set_angle(self, a):
        a = float(a) % 360
        if a == self.angle:
            return
        self.angle = a
        self.scale_angle.set(a)   # triggers _on_angle, which returns here early
        self.lbl_angle.configure(text=f"{a:g}°")
        self._draw_compass()
        self._on_setting_change()

    def _on_levels(self, v):
        v = int(float(v))
        self.levels = None if v >= 256 else v
        self.lbl_levels.configure(text="full" if self.levels is None else str(v))
        self._on_setting_change()

    def _on_thr_lo(self, v):
        v = int(float(v))
        if v == self.thr_lo:
            return
        self.thr_lo = v
        if v > self.thr_hi:
            self.thr_hi = v            # keep the range valid right away
            self.scale_thr_hi.set(v)   # moves the other slider; its handler then sees no change
        self._on_thr_change()

    def _on_thr_hi(self, v):
        v = int(float(v))
        if v == self.thr_hi:
            return
        self.thr_hi = v
        if v < self.thr_lo:
            self.thr_lo = v
            self.scale_thr_lo.set(v)
        self._on_thr_change()

    def _on_thr_change(self):
        self.lbl_thr.configure(text=f"{self.thr_lo}–{self.thr_hi}%")
        if self.run is not None:
            self._cancel_run()
        self.set_mode(AFFECTED)        # shows right away which pixels will be sorted

    def _on_speed(self, v):
        self.speed = int(float(v))
        self.lbl_speed.configure(text=f"{self.speed}×")

    def _on_brush(self, v):
        self.brush = int(float(v))
        self.lbl_brush.configure(text=f"{self.brush} px")

    def set_prop(self, key):
        if key == self.prop:
            return
        self.prop = key
        self._mark(self.prop_buttons, key)
        self._on_setting_change()

    def set_algo(self, key):
        if key == self.algo:
            return
        self.algo = key
        self._mark(self.algo_buttons, key)
        self._on_setting_change()

    def set_mask_mode(self, key):
        self.protect = key == "protect"
        self._mark(self.mask_buttons, key)
        self._on_mask_change()

    def _on_setting_change(self):
        if self.run is not None:
            self._cancel_run()
            self._status("Settings changed: the next animation starts from the last committed state.")
        # Keep the Affected view open while tuning; any other view returns to the animation.
        self.set_mode(AFFECTED if self.mode == AFFECTED else ANIMATION)

    def _on_mask_change(self):
        if self.run is not None:
            self._cancel_run()
        self._draw()

    def _draw_compass(self):
        c = self.compass
        c.delete("all")
        cx = cy = 48
        r = 42
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=BORDER, width=2)
        for deg in (0, 90, 180, 270):
            rad = math.radians(deg)
            c.create_line(cx + (r - 6) * math.cos(rad), cy - (r - 6) * math.sin(rad),
                          cx + r * math.cos(rad), cy - r * math.sin(rad), fill=MUTED)
        rad = math.radians(self.angle)
        L = r - 10
        c.create_line(cx, cy, cx + L * math.cos(rad), cy - L * math.sin(rad),
                      fill=ACCENT, width=3, arrow=tk.LAST, arrowshape=(10, 12, 4))
        c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=TEXT, outline="")

    # ---------- mask ----------

    def clear_mask(self):
        if self.src is None:
            return
        self.mask = None
        self._disp = None
        self._on_mask_change()
        self._status("Mask cleared.")

    def invert_mask(self):
        if self.src is None:
            return
        base = self.mask if self.mask is not None else Image.new("L", self.src.size, 0)
        self.mask = ImageOps.invert(base)
        self._disp = None
        self._on_mask_change()
        self._status("Selection inverted.")

    def _region_full(self):
        """Area where sorting is allowed, at full resolution. None = the whole image."""
        if self.mask is None:
            return None
        m = np.array(self.mask) > 127
        return ~m if self.protect else m

    def _work_region(self, region):
        """The same area at working resolution (threshold: at least 25% of a pixel)."""
        if region is None:
            return None
        small = Image.fromarray(region.astype(np.uint8) * 255).resize(self.base.shape[1::-1], Image.BOX)
        return np.array(small) >= 64

    def _layout(self):
        """Sets the preview position and the cached mask shown on the canvas."""
        dw, dh, x0, y0, s = self._fit(self.src.size)
        self._origin, self._scale = (x0, y0), s
        if self._disp is None or self._disp["size"] != (dw, dh):
            src_mask = self.mask if self.mask is not None else Image.new("L", self.src.size, 0)
            self._disp = {"size": (dw, dh), "mask": maskmod.downscale(src_mask, (dw, dh))}

    def _draw_mask_view(self):
        self._layout()
        dw, dh = self._disp["size"]
        base = np.array(Image.fromarray(self.base).resize((dw, dh), Image.BILINEAR))
        inside = np.array(self._disp["mask"]) > 127
        if not inside.any():
            arr = base
            caption = "MASK · nothing selected"
        else:
            color = hex_to_rgb(WARM) if self.protect else hex_to_rgb(ACCENT)
            arr = maskmod.overlay(base, inside, color)
            kind = "protected" if self.protect else "sorted"
            caption = f"MASK · {kind}: {inside.mean() * 100:.0f}% of the image"
        x0, y0 = self._origin
        self._place(Image.fromarray(arr), x0, y0, caption)

    def _paint_start(self, e, add):
        if self.mode != MASK or self.src is None:
            return
        self._layout()
        if self.run is not None:
            self._cancel_run()
        if self.mask is None:
            self.mask = Image.new("L", self.src.size, 0)
        self._painting = True
        self._paint_value = 255 if add else 0
        self._last = None
        self._paint_point(e.x, e.y)
        self._draw_mask_view()
        self._draw_cursor(e.x, e.y)

    def _paint_move(self, e):
        if not self._painting:
            return
        self._paint_point(e.x, e.y)
        self._draw_mask_view()
        self._draw_cursor(e.x, e.y)

    def _paint_end(self, e=None):
        self._painting = False
        self._last = None

    def _paint_point(self, ex, ey):
        """Paints from the previous point to the current one, in the preview and in the full mask."""
        x0, y0 = self._origin
        s = self._scale
        dx, dy = ex - x0, ey - y0
        sx, sy = dx / s, dy / s
        ldx, ldy, lsx, lsy = self._last or (dx, dy, sx, sy)
        v = self._paint_value
        maskmod.stroke(self._disp["mask"], (ldx, ldy), (dx, dy), self.brush, v)
        maskmod.stroke(self.mask, (lsx, lsy), (sx, sy), self.brush / s, v)
        self._last = (dx, dy, sx, sy)

    def _draw_cursor(self, x, y):
        self.stage.delete("cursor")
        if self.mode == MASK and self.src is not None:
            r = self.brush
            self.stage.create_oval(x - r, y - r, x + r, y + r, outline=TEXT, dash=(4, 3), tags="cursor")

    # ---------- passes and history ----------

    def _work_size(self):
        return self.base.shape[1::-1]

    def _start_run(self):
        spec = Spec(self.angle, self.prop, self.levels, self.reverse.get(), self.algo,
                    self._region_full(), self.thr_lo / 100, self.thr_hi / 100)
        region = sorter.effective_region(self.base, self._work_region(spec.region),
                                         spec.prop, spec.lo, spec.hi)
        segs = sorter.build_segments(self.base.shape[:2], spec.angle, region)
        if not segs:
            self._status("The mask and threshold leave nothing to sort.")
            return False
        ck = sorter.sort_keys(self.base.reshape(-1, 3), spec.prop, spec.levels, spec.reverse)
        self.run = sorter.Animator(self.base, segs, ck, spec.algo)
        self.run_spec = spec
        self.progress["value"] = 0
        return True

    def _stop_timer(self):
        self.running = False
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None

    def _cancel_run(self):
        self._stop_timer()
        self.run = None
        self.run_spec = None
        self.progress["value"] = 0
        self.btn_play.set_text("▶  Play")

    def toggle_play(self):
        if self.src is None:
            self._status("Choose an image first.")
            return
        if self.running:
            self._stop_timer()
            self.btn_play.set_text("▶  Resume")
            self._status("Paused. Changing a setting cancels this pass.")
            return
        if self.run is None and not self._start_run():
            return
        self.running = True
        self.btn_play.set_text("❚❚  Pause")
        self.set_mode(ANIMATION)
        self._tick()

    def _tick(self):
        self._after_id = None
        if not self.running or self.run is None:
            return
        for _ in range(self.speed):
            self.run.advance()
            if self.run.finished:
                break
        if self.run.finished:
            self._finish_run()
            return
        self.progress["value"] = 100.0 * self.run.steps_done / max(1, self.run.total)
        self._status(f"Sorting step {self.run.steps_done} / {self.run.total}")
        if self.mode == ANIMATION:
            self._show(self.run.render(), self._caption())
        elif self.mode == MOTION:
            self._draw_map(self.run.activity_map())
        self._after_id = self.after(TICK_MS, self._tick)

    def _finish_run(self):
        run, spec = self.run, self.run_spec
        final = run.render()
        self.last_heat = run.activity_map()
        self._cancel_run()
        if np.array_equal(final, self.base):
            self._status("This direction and key no longer change anything.")
        else:
            self.history.append((self.base, list(self.chain)))
            self.base = final
            self.chain.append(spec)
            self.full_cache = None
            self._status(f"Pass {len(self.chain)} committed. Change the direction or key and play again.")
        self.progress["value"] = 100
        self._refresh_chain()
        self.set_mode(ANIMATION)

    def undo(self):
        if self.src is None:
            return
        if not self.history:
            self._status("Nothing to undo.")
            return
        self._cancel_run()
        self.base, self.chain = self.history.pop()
        self.full_cache = None
        self._refresh_chain()
        self.set_mode(ANIMATION)
        self._status(f"Undone. Committed passes: {len(self.chain)}.")

    def reset_to_original(self):
        if self.src is None or not self.chain:
            return
        self._cancel_run()
        self.history.append((self.base, list(self.chain)))
        self.base, self.chain = self.start, []
        self.full_cache = None
        self.last_heat = None
        self._refresh_chain()
        self.set_mode(ANIMATION)
        self._status("Back to the original. Undo restores the previous state.")

    def _refresh_chain(self):
        n = len(self.chain)
        self.lbl_chain.configure(text=f"Committed passes: {n}" if n else "State: original")

    def _full_state(self):
        """Full-resolution result: replays the committed passes on the original (cached)."""
        if self.full_cache is None:
            self._status("Computing at full resolution…")
            self.update_idletasks()
            rgb = np.array(self.src)
            for spec in self.chain:
                rgb = sorter.run_fast(rgb, spec.angle, spec.prop, spec.levels, spec.reverse,
                                      spec.region, spec.lo, spec.hi)
            self.full_cache = rgb
        return self.full_cache

    # ---------- views ----------

    def set_mode(self, mode):
        self.mode = mode
        for name, b in self.mode_buttons.items():
            b.select(name == mode)
        self._draw()
        if mode == MASK and self.src is not None:
            self._status("LMB paints · RMB or Shift+LMB erases · the circle shows the brush size")

    def _draw(self):
        if self.src is None:
            self._placeholder("Choose an image in the left panel\n(or press O)")
            return
        if self.mode == ANIMATION:
            frame = self.run.render() if self.run is not None else self.base
            self._show(frame, self._caption())
        elif self.mode == RESULT:
            self._show(self._full_state(), f"RESULT · {len(self.chain)} passes · full resolution")
        elif self.mode == MOTION:
            heat = self.run.activity_map() if self.run is not None else self.last_heat
            if heat is None:
                self._placeholder("Run a sort to see the motion map")
            else:
                self._draw_map(heat)
        elif self.mode == AFFECTED:
            self._draw_affected()
        else:
            self._draw_mask_view()

    def _draw_affected(self):
        """Preview of the area that will be sorted (mask and threshold). The rest is dimmed."""
        region = sorter.effective_region(self.base, self._work_region(self._region_full()),
                                         self.prop, self.thr_lo / 100, self.thr_hi / 100)
        if region is None:
            self._show(self.base, "AFFECTED · the whole image is sorted (100%)")
            return
        dimmed = maskmod.overlay(self.base, ~region, hex_to_rgb(BG), 0.7)
        self._show(dimmed, f"AFFECTED · sorted: {region.mean() * 100:.0f}% of the image · the rest is dimmed")

    def _caption(self):
        if self.run is not None:
            return (f"ANIMATION · {self.angle:g}° · {_label(sorter.PROPERTIES, self.prop)} · "
                    f"step {self.run.steps_done}/{self.run.total}")
        return f"STATE · {len(self.chain)} passes"

    def _draw_map(self, heat):
        peak = float(heat.max())
        idx = (heat / peak * 255).astype(np.uint8) if peak > 0 else np.zeros(heat.shape, np.uint8)
        self._show(PALETTE[idx], "MOTION MAP · bright spots changed pixels most often")

    def _stage_size(self):
        return max(self.stage.winfo_width(), 50), max(self.stage.winfo_height(), 50)

    def _fit(self, size):
        """(width, height, x0, y0, scale) of an image of `size`, centered on the canvas."""
        w, h = self._stage_size()
        s = min((w - 24) / size[0], (h - 24) / size[1])
        dw, dh = max(1, round(size[0] * s)), max(1, round(size[1] * s))
        return dw, dh, (w - dw) // 2, (h - dh) // 2, dw / size[0]

    def _placeholder(self, text):
        self.stage.delete("all")
        w, h = self._stage_size()
        self.stage.create_text(w // 2, h // 2, text=text, fill=MUTED, font=FONT, justify="center")

    def _place(self, pil, x0, y0, caption):
        self._photo = ImageTk.PhotoImage(pil)
        self.stage.delete("all")
        self.stage.create_rectangle(x0 - 1, y0 - 1, x0 + pil.width, y0 + pil.height, outline=BORDER)
        self.stage.create_image(x0, y0, image=self._photo, anchor="nw")
        self.stage.create_text(16, 12, anchor="nw", fill=MUTED, font=FONT_SMALL, text=caption)

    def _show(self, data, caption):
        img = data if isinstance(data, Image.Image) else Image.fromarray(data)
        dw, dh, x0, y0, _ = self._fit(img.size)
        resample = Image.NEAREST if dw >= img.width else Image.BILINEAR
        self._place(img.resize((dw, dh), resample), x0, y0, caption)

    # ---------- files ----------

    def open_image(self):
        path = filedialog.askopenfilename(title="Choose an image", filetypes=IMG_TYPES)
        if not path:
            return
        try:
            img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        except Exception as exc:
            messagebox.showerror("Could not load the image", str(exc))
            return
        self._cancel_run()
        self.src = img
        work = img.copy()
        work.thumbnail((WORK_MAX, WORK_MAX), Image.LANCZOS)
        self.start = np.array(work)
        self.base = self.start
        self.chain, self.history = [], []
        self.full_cache = None
        self.last_heat = None
        self.mask = None
        self._disp = None
        self.lbl_file.configure(text=f"{os.path.basename(path)}\n{img.width} × {img.height} px", fg=TEXT)
        self._refresh_chain()
        self.set_mode(ANIMATION)
        self._status("Image loaded. Paint an area in the Mask view, or pick a direction and press Play.")

    def save_result(self):
        if self.src is None:
            self._status("Choose an image first.")
            return
        path = filedialog.asksaveasfilename(title="Save result", defaultextension=".png",
                                            initialfile="pixel_sort.png",
                                            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")])
        if not path:
            return
        Image.fromarray(self._full_state()).save(path)
        self._status(f"Saved: {os.path.basename(path)}")

    def save_gif(self):
        if self.src is None:
            self._status("Choose an image first.")
            return
        path = filedialog.asksaveasfilename(title="Save animation", defaultextension=".gif",
                                            initialfile="pixel_sort.gif", filetypes=[("GIF", "*.gif")])
        if not path:
            return
        self._status("Recording animation…")
        self.update_idletasks()
        frames = sorter.record(self.base, self.angle, self.prop, self.levels, self.reverse.get(),
                               self.algo, self._work_region(self._region_full()),
                               self.thr_lo / 100, self.thr_hi / 100)
        imgs = [Image.fromarray(f) for f in frames]
        imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=45, loop=0)
        self._status(f"Saved animation: {os.path.basename(path)}")

    def _status(self, text):
        self.lbl_status.configure(text=text)
