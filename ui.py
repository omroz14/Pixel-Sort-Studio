"""Application window: control panels, preview, mask, animation and step history."""
import math
import os
import tkinter as tk
from collections import namedtuple
from tkinter import filedialog, messagebox

import customtkinter as ctk
import numpy as np
from PIL import Image, ImageOps, ImageTk

import mask as maskmod
import sorter
from theme import *

WORK_MAX = 640   # side of the working copy (px); the preview and the animation run on it
TICK_MS = 33     # ~30 frames per second
PANEL_W = 290    # width of each side panel (px)

ANIMATION, RESULT, MOTION, MASK, AFFECTED = "Animation", "Result", "Motion map", "Mask", "Affected"
MODES = (ANIMATION, RESULT, MOTION, MASK, AFFECTED)
IMG_TYPES = [("Images", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"), ("All files", "*.*")]
MASK_OPTIONS = (("protect", "Protect"), ("sort", "Sort only"))
PRESETS = (("→", 0), ("↗", 45), ("↑", 90), ("↖", 135), ("←", 180), ("↙", 225), ("↓", 270), ("↘", 315))
ALGO_NOTES = {
    "odd_even": "Even pairs, then odd pairs. Smooth, steady motion.",
    "bitonic": "A sorting network: the fewest steps, with long jumps.",
    "bubble": "One sweep per step. The slowest, but the most spectacular.",
}

# A committed pass: settings, the mask at full resolution and the property threshold (lo, hi in 0..1).
# The threshold is evaluated when the pass runs, on the image of that moment, so only lo and hi are stored.
Spec = namedtuple("Spec", "angle prop levels reverse algo region lo hi")


def _make_palette():
    """Motion map palette: background -> violet -> warm orange -> white."""
    stops = [hex_to_rgb(SURFACE), hex_to_rgb(ACCENT), hex_to_rgb(WARM), (255, 255, 255)]
    pos = [0.0, 0.4, 0.75, 1.0]
    xs = np.linspace(0, 1, 256)
    lut = np.zeros((256, 3), np.uint8)
    for c in range(3):
        lut[:, c] = np.interp(xs, pos, [s[c] for s in stops])
    return lut


PALETTE = _make_palette()


def _label(options, key):
    return dict(options)[key]


def _key(options, label):
    return next(k for k, v in options if v == label)


class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=BG)
        self.title("Pixel Sort Studio")
        self.geometry("1400x860")
        self.minsize(1120, 680)

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
        self.animate = tk.BooleanVar(value=False)   # step-by-step animation; off = instant result
        self.brush = 60
        self.speed = 2

        self.run = None           # active pass (sorter.Animator)
        self.run_spec = None      # settings of the active pass
        self.running = False
        self.mode = ANIMATION
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
        self._on_animate()
        self._refresh_chain()
        self.set_mode(ANIMATION)

    # ---------- building the interface ----------

    def _make_panel(self, column):
        """A scrollable side panel in grid `column`. Returns the frame to put cards in."""
        panel = ctk.CTkScrollableFrame(self, width=PANEL_W, fg_color=BG, corner_radius=0,
                                       scrollbar_button_color=BG, scrollbar_button_hover_color=BORDER)
        panel.grid(row=0, column=column, sticky="ns", padx=(12, 0) if column == 0 else (0, 12), pady=12)
        return panel

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        left = self._make_panel(0)
        right = self._make_panel(2)
        self._build_left(left)
        self._build_right(right)
        self._build_main()

    def _build_left(self, left):
        """What to sort and how: image, direction, key, threshold."""
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.pack(fill="x", pady=(4, 14), padx=4)
        ctk.CTkLabel(head, text="Pixel Sort Studio", font=FONT_TITLE, text_color=TEXT, height=26).pack(anchor="w")
        ctk.CTkLabel(head, text="sort pixels along any direction", font=FONT_SMALL,
                     text_color=MUTED, height=16).pack(anchor="w")

        # IMAGE
        c = card(left, "IMAGE", "key O")
        button(c, "Open image…", self.open_image).pack(fill="x", padx=PAD)
        self.lbl_file = ctk.CTkLabel(c, text="No file loaded", font=FONT_SMALL, text_color=MUTED,
                                     justify="left", anchor="w")
        self.lbl_file.pack(fill="x", padx=PAD, pady=(6, PAD))

        # DIRECTION
        c = card(left, "DIRECTION")
        top = ctk.CTkFrame(c, fg_color="transparent")
        top.pack(fill="x", padx=PAD)
        self.compass = tk.Canvas(top, width=84, height=84, bg=SURFACE, highlightthickness=0)
        self.compass.pack(side="left")
        info = ctk.CTkFrame(top, fg_color="transparent")
        info.pack(side="left", padx=(14, 0), fill="both", expand=True)
        self.lbl_angle = ctk.CTkLabel(info, text="0°", font=FONT_BIG, text_color=TEXT, anchor="w")
        self.lbl_angle.pack(anchor="w", pady=(10, 0))
        ctk.CTkLabel(info, text="0° right · 90° up", font=FONT_SMALL, text_color=MUTED,
                     height=16, anchor="w").pack(anchor="w")
        self.scale_angle = ctk.CTkSlider(c, from_=0, to=359, number_of_steps=359, height=16,
                                         fg_color=SURFACE_2, progress_color=ACCENT, button_color=TEXT,
                                         button_hover_color="#ffffff",
                                         command=lambda v: self.set_angle(int(round(v))))
        self.scale_angle.set(0)
        self.scale_angle.pack(fill="x", padx=PAD - 4, pady=(10, 8))
        self.presets = segmented(c, [a for a, _ in PRESETS],
                                 lambda v: self.set_angle(dict(PRESETS)[v]))
        self.presets.pack(fill="x", padx=PAD, pady=(0, PAD))

        # SORT BY
        c = card(left, "SORT BY")
        self.prop_grid = ChoiceGrid(c, sorter.PROPERTIES, self.prop, self.set_prop, cols=2)
        switch(c, "Descending order", self.reverse, self._on_setting_change).pack(
            anchor="w", padx=PAD, pady=(10, 6))
        self.row_levels = SliderRow(c, "Key quantization", 8, 256, 256, self._on_levels,
                                    fmt=lambda v: "full" if v >= 256 else f"{v} levels")
        note(c, "Fewer levels give a coarser sort. Pixels in one level keep their order.", pady=(0, PAD))

        # THRESHOLD
        c = card(left, "THRESHOLD")
        self.row_thr_lo = SliderRow(c, "From", 0, 100, self.thr_lo, self._on_thr_lo, fmt=lambda v: f"{v}%")
        self.row_thr_hi = SliderRow(c, "To", 0, 100, self.thr_hi, self._on_thr_hi, fmt=lambda v: f"{v}%")
        note(c, "Only pixels whose “Sort by” value is in this range move. The rest stay put and "
                "split lines into segments. Preview them in the Affected view.", pady=(0, PAD))

    def _build_right(self, right):
        """Mask, animation, history and export."""
        # MASK
        c = card(right, "MASK", "key M")
        self.mask_tabs = segmented(c, [lbl for _, lbl in MASK_OPTIONS],
                                   lambda v: self.set_mask_mode(_key(MASK_OPTIONS, v)))
        self.mask_tabs.set(_label(MASK_OPTIONS, "protect"))
        self.mask_tabs.pack(fill="x", padx=PAD, pady=(0, 6))
        self.row_brush = SliderRow(c, "Brush size", 2, 200, self.brush, self._on_brush, fmt=lambda v: f"{v} px")
        mb = ctk.CTkFrame(c, fg_color="transparent")
        mb.pack(fill="x", padx=PAD, pady=(6, 0))
        button(mb, "Clear", self.clear_mask, width=10).pack(side="left", fill="x", expand=True)
        button(mb, "Invert", self.invert_mask, width=10).pack(side="left", fill="x", expand=True, padx=(6, 0))
        note(c, "Paint in the Mask view: left button paints, right button or Shift erases.", pady=(8, PAD))

        # ANIMATION: the algorithm and the speed only matter when the pass is animated
        c = card(right, "ANIMATION")
        switch(c, "Animate the sorting", self.animate, self._on_animate).pack(anchor="w", padx=PAD, pady=(0, 2))
        self.lbl_anim = note(c, "", pady=(2, 10))
        self.lbl_algo_head = ctk.CTkLabel(c, text="Algorithm", font=FONT, text_color=TEXT, height=20, anchor="w")
        self.lbl_algo_head.pack(fill="x", padx=PAD)
        self.algo_tabs = segmented(c, [lbl for _, lbl in sorter.ALGORITHMS],
                                   lambda v: self.set_algo(_key(sorter.ALGORITHMS, v)))
        self.algo_tabs.set(_label(sorter.ALGORITHMS, self.algo))
        self.algo_tabs.pack(fill="x", padx=PAD, pady=(4, 0))
        self.lbl_algo = note(c, ALGO_NOTES[self.algo], pady=(6, 6))
        self.row_speed = SliderRow(c, "Speed", 1, 40, self.speed, self._on_speed, fmt=lambda v: f"{v}×")
        ctk.CTkFrame(c, fg_color="transparent", height=PAD - 4).pack()

        # HISTORY
        c = card(right, "HISTORY")
        hist = ctk.CTkFrame(c, fg_color="transparent")
        hist.pack(fill="x", padx=PAD)
        button(hist, "↶  Undo", self.undo, width=10).pack(side="left", fill="x", expand=True)
        button(hist, "↺  Original", self.reset_to_original, width=10).pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.lbl_chain = ctk.CTkLabel(c, text="", font=FONT_SMALL, text_color=MUTED, justify="left", anchor="w")
        self.lbl_chain.pack(fill="x", padx=PAD, pady=(8, PAD))

        # EXPORT
        c = card(right, "EXPORT")
        ex = ctk.CTkFrame(c, fg_color="transparent")
        ex.pack(fill="x", padx=PAD, pady=(0, PAD))
        button(ex, "Save image", self.save_result, width=10).pack(side="left", fill="x", expand=True)
        button(ex, "Save GIF", self.save_gif, width=10).pack(side="left", fill="x", expand=True, padx=(6, 0))

    def _build_main(self):
        """The center: view tabs, the stage and the play bar."""
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self.tabs = segmented(main, MODES, self.set_mode)
        self.tabs.grid(row=0, column=0, sticky="w", pady=(4, 10))
        ctk.CTkLabel(main, text="Space play · 1–5 views · Ctrl+Z undo", font=FONT_SMALL,
                     text_color=MUTED).grid(row=0, column=0, sticky="e", pady=(4, 10))

        frame = ctk.CTkFrame(main, fg_color=SURFACE, corner_radius=12)
        frame.grid(row=1, column=0, sticky="nsew")
        self.stage = tk.Canvas(frame, bg=SURFACE, highlightthickness=0)
        self.stage.pack(fill="both", expand=True, padx=8, pady=8)
        self._bind_stage()

        bar = ctk.CTkFrame(main, fg_color=SURFACE, corner_radius=12)
        bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        bar.grid_columnconfigure(1, weight=1)
        self.btn_play = button(bar, "▶  Sort", self.toggle_play, accent=True, width=130, height=38)
        self.btn_play.grid(row=0, column=0, rowspan=2, padx=PAD, pady=PAD - 2)
        self.progress = ctk.CTkProgressBar(bar, height=8, fg_color=SURFACE_2, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.grid(row=0, column=1, sticky="ew", padx=(0, PAD), pady=(PAD, 0))
        self.lbl_status = ctk.CTkLabel(bar, text="", font=FONT_SMALL, text_color=MUTED, anchor="w", height=18)
        self.lbl_status.grid(row=1, column=1, sticky="ew", padx=(0, PAD), pady=(2, PAD - 4))

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
        for i, m in enumerate(MODES, 1):
            self.bind(f"<Key-{i}>", lambda e, m=m: self.set_mode(m))

    # ---------- settings ----------

    def set_angle(self, a):
        a = float(a) % 360
        if a == self.angle:
            return
        self.angle = a
        self.scale_angle.set(a)
        self._draw_compass()
        self._on_setting_change()

    def _on_levels(self, v):
        self.levels = None if v >= 256 else v
        self._on_setting_change()

    def _on_thr_lo(self, v):
        if v == self.thr_lo:
            return
        self.thr_lo = v
        if v > self.thr_hi:
            self.thr_hi = v               # keep the range valid right away
            self.row_thr_hi.set(v)
        self._on_thr_change()

    def _on_thr_hi(self, v):
        if v == self.thr_hi:
            return
        self.thr_hi = v
        if v < self.thr_lo:
            self.thr_lo = v
            self.row_thr_lo.set(v)
        self._on_thr_change()

    def _on_thr_change(self):
        if self.run is not None:
            self._cancel_run()
        self.set_mode(AFFECTED)           # shows right away which pixels will be sorted

    def _on_speed(self, v):
        self.speed = v

    def _on_brush(self, v):
        self.brush = v

    def _on_animate(self):
        on = self.animate.get()
        self.lbl_anim.configure(text="Play shows every step of the algorithm." if on
                                else "Off: Sort applies each pass instantly.")
        color = TEXT if on else MUTED
        self.lbl_algo_head.configure(text_color=color)
        self.algo_tabs.configure(state="normal" if on else "disabled")
        self.row_speed.enable(on)
        if self.run is None:
            self.btn_play.configure(text=self._play_text())

    def _play_text(self):
        return "▶  Play" if self.animate.get() else "▶  Sort"

    def set_prop(self, key):
        if key == self.prop:
            return
        self.prop = key
        self.prop_grid.select(key)
        self._on_setting_change()

    def set_algo(self, key):
        if key == self.algo:
            return
        self.algo = key
        self.algo_tabs.set(_label(sorter.ALGORITHMS, key))
        self.lbl_algo.configure(text=ALGO_NOTES[key])
        self._on_setting_change()

    def set_mask_mode(self, key):
        self.protect = key == "protect"
        self._on_mask_change()

    def _on_setting_change(self):
        if self.run is not None:
            self._cancel_run()
            self._status("Settings changed: the next pass starts from the last committed state.")
        # Keep the Affected view open while tuning; any other view returns to the animation.
        self.set_mode(AFFECTED if self.mode == AFFECTED else ANIMATION)

    def _on_mask_change(self):
        if self.run is not None:
            self._cancel_run()
        self._draw()

    def _draw_compass(self):
        a = self.angle
        self.lbl_angle.configure(text=f"{a:g}°")
        self.presets.set(next((arrow for arrow, deg in PRESETS if deg == a), ""))
        c = self.compass
        c.delete("all")
        cx = cy = 42
        r = 38
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=BORDER, width=2)
        for deg in range(0, 360, 45):
            rad = math.radians(deg)
            inner = r - (7 if deg % 90 == 0 else 4)
            c.create_line(cx + inner * math.cos(rad), cy - inner * math.sin(rad),
                          cx + r * math.cos(rad), cy - r * math.sin(rad), fill=MUTED)
        rad = math.radians(a)
        L = r - 9
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
            caption = "MASK · nothing selected · paint with the left button"
        else:
            color = hex_to_rgb(WARM) if self.protect else hex_to_rgb(ACCENT)
            arr = maskmod.overlay(base, inside, color)
            kind = "protected" if self.protect else "sorted"
            caption = f"MASK · {kind}: {inside.mean() * 100:.0f}% of the image"
        x0, y0 = self._origin
        self._place(Image.fromarray(arr), x0, y0, caption)

    def _paint_start(self, e, add):
        if self.src is None:
            self.open_image()             # the empty stage works as an "open" button
            return
        if self.mode != MASK:
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

    def _make_spec(self):
        """Settings of the next pass, taken from the panels."""
        return Spec(self.angle, self.prop, self.levels, self.reverse.get(), self.algo,
                    self._region_full(), self.thr_lo / 100, self.thr_hi / 100)

    def _start_run(self):
        spec = self._make_spec()
        region = sorter.effective_region(self.base, self._work_region(spec.region),
                                         spec.prop, spec.lo, spec.hi)
        segs = sorter.build_segments(self.base.shape[:2], spec.angle, region)
        if not segs:
            self._status("The mask and threshold leave nothing to sort.")
            return False
        ck = sorter.sort_keys(self.base.reshape(-1, 3), spec.prop, spec.levels, spec.reverse)
        self.run = sorter.Animator(self.base, segs, ck, spec.algo)
        self.run_spec = spec
        self.progress.set(0)
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
        self.progress.set(0)
        self.btn_play.configure(text=self._play_text())

    def toggle_play(self):
        if self.src is None:
            self._status("Open an image first.")
            return
        if self.running:
            self._stop_timer()
            self.btn_play.configure(text="▶  Resume")
            self._status("Paused. Changing a setting cancels this pass.")
            return
        if self.run is None and not self.animate.get():
            self._run_instant()
            return
        if self.run is None and not self._start_run():
            return
        self.running = True
        self.btn_play.configure(text="❚❚  Pause")
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
        self.progress.set(self.run.steps_done / max(1, self.run.total))
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
        self._commit_pass(final, spec)

    def _run_instant(self):
        """Animation off: computes the whole pass at once, without the step-by-step Animator."""
        spec = self._make_spec()
        region = sorter.effective_region(self.base, self._work_region(spec.region),
                                         spec.prop, spec.lo, spec.hi)
        if region is not None and not region.any():
            self._status("The mask and threshold leave nothing to sort.")
            return
        final = sorter.run_fast(self.base, spec.angle, spec.prop, spec.levels, spec.reverse, region)
        self.last_heat = None          # the motion map needs the step-by-step run
        self._commit_pass(final, spec)

    def _commit_pass(self, final, spec):
        """Makes a finished pass the new committed state, or reports that it changed nothing."""
        if np.array_equal(final, self.base):
            self._status("This direction and key no longer change anything.")
        else:
            self.history.append((self.base, list(self.chain)))
            self.base = final
            self.chain.append(spec)
            self.full_cache = None
            self._status(f"Pass {len(self.chain)} committed. Change the direction or key and sort again.")
        self.progress.set(1)
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
        """Lists the committed passes (the last few) under the history buttons."""
        if not self.chain:
            self.lbl_chain.configure(text="Original image, no passes yet")
            return
        lines = [f"{i}.  {s.angle:g}° · {_label(sorter.PROPERTIES, s.prop)}"
                 f"{' ↓' if s.reverse else ''}{'' if s.lo <= 0 and s.hi >= 1 else f' · {s.lo:.0%}–{s.hi:.0%}'}"
                 for i, s in enumerate(self.chain, 1)]
        if len(lines) > 6:
            lines = [f"… {len(lines) - 5} earlier"] + lines[-5:]
        self.lbl_chain.configure(text="\n".join(lines))

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
        self.tabs.set(mode)
        self._draw()
        if mode == MASK and self.src is not None:
            self._status("Left button paints · right button or Shift erases · the circle is the brush")

    def _draw(self):
        if self.src is None:
            self._placeholder("Click here or press O to open an image")
            return
        if self.mode == ANIMATION:
            frame = self.run.render() if self.run is not None else self.base
            self._show(frame, self._caption())
        elif self.mode == RESULT:
            self._show(self._full_state(), f"RESULT · {len(self.chain)} passes · full resolution")
        elif self.mode == MOTION:
            heat = self.run.activity_map() if self.run is not None else self.last_heat
            if heat is None:
                self._placeholder("Turn on “Animate the sorting” and play a pass\nto see where pixels moved")
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
        return f"PREVIEW · {len(self.chain)} passes"

    def _draw_map(self, heat):
        peak = float(heat.max())
        idx = (heat / peak * 255).astype(np.uint8) if peak > 0 else np.zeros(heat.shape, np.uint8)
        self._show(PALETTE[idx], "MOTION MAP · bright spots changed most often")

    def _stage_size(self):
        return max(self.stage.winfo_width(), 50), max(self.stage.winfo_height(), 50)

    def _fit(self, size):
        """(width, height, x0, y0, scale) of an image of `size`, centered on the canvas."""
        w, h = self._stage_size()
        top = 30                           # room for the caption
        s = min((w - 24) / size[0], (h - top - 12) / size[1])
        dw, dh = max(1, round(size[0] * s)), max(1, round(size[1] * s))
        return dw, dh, (w - dw) // 2, top + (h - top - 12 - dh) // 2, dw / size[0]

    def _placeholder(self, text):
        self.stage.delete("all")
        w, h = self._stage_size()
        bw, bh = min(420, w - 40), min(220, h - 40)
        self.stage.create_rectangle(w // 2 - bw // 2, h // 2 - bh // 2, w // 2 + bw // 2, h // 2 + bh // 2,
                                    outline=BORDER, dash=(6, 4), width=2)
        self.stage.create_text(w // 2, h // 2 - 22, text="🖼", fill=MUTED, font=(FAMILY, -32))
        self.stage.create_text(w // 2, h // 2 + 22, text=text, fill=MUTED, font=FONT, justify="center")

    def _place(self, pil, x0, y0, caption):
        self._photo = ImageTk.PhotoImage(pil)
        self.stage.delete("all")
        self.stage.create_image(x0, y0, image=self._photo, anchor="nw")
        self.stage.create_text(10, 6, anchor="nw", fill=MUTED, font=FONT_SECTION, text=caption)

    def _show(self, data, caption):
        img = data if isinstance(data, Image.Image) else Image.fromarray(data)
        dw, dh, x0, y0, _ = self._fit(img.size)
        resample = Image.NEAREST if dw >= img.width else Image.BILINEAR
        self._place(img.resize((dw, dh), resample), x0, y0, caption)

    # ---------- files ----------

    def open_image(self):
        path = filedialog.askopenfilename(title="Open an image", filetypes=IMG_TYPES)
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
        self.lbl_file.configure(text=f"{os.path.basename(path)}\n{img.width} × {img.height} px", text_color=TEXT)
        self._refresh_chain()
        self.set_mode(ANIMATION)
        self._status("Image loaded. Pick a direction and a key, then press Sort.")

    def save_result(self):
        if self.src is None:
            self._status("Open an image first.")
            return
        path = filedialog.asksaveasfilename(title="Save image", defaultextension=".png",
                                            initialfile="pixel_sort.png",
                                            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")])
        if not path:
            return
        Image.fromarray(self._full_state()).save(path)
        self._status(f"Saved: {os.path.basename(path)}")

    def save_gif(self):
        if self.src is None:
            self._status("Open an image first.")
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
