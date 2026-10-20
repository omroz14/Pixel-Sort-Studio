"""Pixel sorting along a direction vector. The image is never rotated.

Pixels are grouped into lines parallel to the direction and each line is sorted along itself.
The sort key is a chosen property (brightness, hue, saturation, value), optionally quantized.
Every pixel gets a unique key (property first, then pixel index), so the animation and the
fast result always produce the identical order.

Threshold: only pixels whose property lies in [lo, hi] (0..1) are sorted. The others stay in
place and break the line into separate segments, so sorting happens in independent runs.
"""
import math

import numpy as np

PROPERTIES = (("luma", "Brightness"), ("hue", "Hue"), ("sat", "Saturation"), ("val", "Value"))
ALGORITHMS = (("odd_even", "Odd-even"), ("bitonic", "Bitonic"), ("bubble", "Bubble"))
QUIET_LIMIT = {"odd_even": 2, "bubble": 1, "bitonic": None}
LUM = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def direction(angle):
    """Angle in degrees -> vector (dx, dy). 0° = right, 90° = up, 45° = upper right."""
    rad = math.radians(angle)
    dx, dy = math.cos(rad), -math.sin(rad)   # the image y axis grows downwards
    m = max(abs(dx), abs(dy))
    return dx / m, dy / m


def build_segments(shape, angle, region=None):
    """Segments (arrays of pixel indices), each contiguous and ordered along the direction.

    region: bool (H, W), True = may be sorted; None = the whole image.
    """
    H, W = shape
    dx, dy = direction(angle)
    xs = np.tile(np.arange(W, dtype=np.float32), H)
    ys = np.repeat(np.arange(H, dtype=np.float32), W)
    line = np.round(-xs * dy + ys * dx).astype(np.int32)   # index of the line parallel to the direction
    pos = xs * dx + ys * dy                                  # position along the direction
    order = np.lexsort((pos, line))
    inside = np.ones(H * W, bool) if region is None else region.ravel()
    segments = []
    for idx in np.split(order, np.flatnonzero(np.diff(line[order])) + 1):
        ins = inside[idx]
        if not ins.any():
            continue
        c = np.concatenate(([0], ins.astype(np.int8), [0]))
        edges = np.flatnonzero(np.diff(c))
        segments.extend(idx[a:b] for a, b in zip(edges[::2], edges[1::2]))
    return segments


def _unit(flat, prop):
    """Property of every pixel, normalized to [0, 1]."""
    f = flat.astype(np.float32) * np.float32(1.0 / 255.0)
    if prop == "luma":
        return f @ LUM
    mx = f.max(axis=1)
    if prop == "val":
        return mx
    mn = f.min(axis=1)
    d = mx - mn
    if prop == "sat":
        return np.where(mx > 0, d / np.maximum(mx, 1e-9), 0.0).astype(np.float32)
    r, g, b = f[:, 0], f[:, 1], f[:, 2]
    dd = np.where(d > 1e-9, d, 1.0)
    h = np.select([mx == r, mx == g], [(g - b) / dd, (b - r) / dd + 2.0], default=(r - g) / dd + 4.0)
    return np.where(d > 1e-9, np.mod(h / 6.0, 1.0), 0.0).astype(np.float32)


def sort_keys(flat, prop, levels, reverse):
    """Unique key per pixel: the property (optionally quantized), then the pixel index.

    Quantization: with `levels` buckets, the property u in [0, 1] becomes
    floor(u * levels), clipped to levels - 1. Pixels in the same bucket tie on the property,
    so the pixel index decides and they keep their original relative order. Fewer levels give
    a coarser sort that preserves more of the original structure. Without quantization 65535
    buckets are used, which is practically exact.
    """
    u = np.clip(_unit(flat, prop), 0.0, 1.0)
    if levels:
        bucket = np.minimum(np.floor(u * levels), levels - 1)
    else:
        bucket = np.floor(u * 65535)
    bucket = bucket.astype(np.float64)
    if reverse:
        bucket = -bucket
    n = flat.shape[0]
    return bucket * n + np.arange(n, dtype=np.float64)


def effective_region(rgb, region, prop, lo=0.0, hi=1.0):
    """Area where sorting is allowed: the user mask intersected with the property threshold.

    A pixel passes the threshold when its property (0..1) lies in [lo, hi]. The threshold is
    evaluated on the current image, because after every pass the colors sit elsewhere. Sorting
    only permutes pixels inside segments, and the segments consist of in-range pixels, so the
    area does not change after a pass with the same property.

    region: bool (H, W) or None. Returns bool (H, W) or None (= the whole image).
    """
    if lo <= 0.0 and hi >= 1.0:
        return region
    H, W = rgb.shape[:2]
    u = np.clip(_unit(rgb.reshape(-1, 3), prop), 0.0, 1.0).reshape(H, W)
    inside = (u >= lo) & (u <= hi)
    return inside if region is None else region & inside


def sort_fast(flat, segments, ck):
    """Sorts every segment at once, without animation."""
    out = flat.copy()
    for seg in segments:
        out[seg] = flat[seg[np.argsort(ck[seg])]]
    return out


def run_fast(rgb, angle, prop, levels, reverse, region=None, lo=0.0, hi=1.0):
    """Full pass without animation. Used for the full-resolution result."""
    flat = rgb.reshape(-1, 3)
    region = effective_region(rgb, region, prop, lo, hi)
    segs = build_segments(rgb.shape[:2], angle, region)
    out = sort_fast(flat, segs, sort_keys(flat, prop, levels, reverse))
    return out.reshape(rgb.shape)


def _next_pow2(m):
    return 1 << (m - 1).bit_length()


def _total(algo, P):
    if P < 2:
        return 0
    if algo == "odd_even":
        return P
    if algo == "bubble":
        return P - 1
    L = P.bit_length() - 1
    return L * (L + 1) // 2


def _steps(algo, P):
    """Generator of steps. Each step is a list of compare-exchange operations (a, b, ascending).

    Every comparator is ascending: the smaller key always goes to the lower index. The Animator
    relies on this, because the padding (+inf) at the end of a short segment then never moves,
    so every intermediate state can be rendered.
    """
    if algo == "odd_even":
        for p in range(P):
            a = np.arange(p % 2, P - 1, 2)
            yield [(a, a + 1, True)] if a.size else []
    elif algo == "bubble":
        for p in range(P - 1):
            yield [(j, j + 1, True) for j in range(P - 1 - p)]
    else:  # bitonic: a network with O(log² n) stages
        # Variant with only ascending comparators. The first step of each merge stage compares
        # every element with its mirror image inside the block of size k (index ^ (k - 1));
        # the following steps are the usual half-cleaners (index ^ j). The classic version
        # alternates the direction per block, which would pull the padding into visible slots.
        idx = np.arange(P)

        def step(partner):
            keep = partner > idx
            return [(idx[keep], partner[keep], True)]

        k = 2
        while k <= P:
            yield step(idx ^ (k - 1))
            j = k // 4
            while j >= 1:
                yield step(idx ^ j)
                j //= 2
            k *= 2


class Animator:
    """Runs the chosen algorithm step by step, on all segments at once."""

    def __init__(self, rgb, segments, ck, algo):
        self.shape = rgb.shape
        self.flat = rgb.reshape(-1, 3)
        N = self.flat.shape[0]
        n = len(segments)
        M = max((s.size for s in segments), default=0)
        P = _next_pow2(M) if algo == "bitonic" else M
        self.total = _total(algo, P)
        self.finished = n == 0 or P < 2
        self.steps_done = 0
        self.quiet_limit = QUIET_LIMIT[algo]
        self._quiet = 0
        self._gen = _steps(algo, P)
        # slot[i, j]: which pixel physically sits at position j of segment i (-1 = none)
        self.slot = np.full((n, P), -1, np.int64)
        for i, seg in enumerate(segments):
            self.slot[i, :seg.size] = seg
        self.valid = self.slot >= 0
        self.C = np.where(self.valid, self.slot, N)      # which pixel is at the position (N = empty)
        self.ck = np.append(ck, np.inf)                  # an empty position always sorts to the end
        self.activity = np.zeros((n, P), np.float32)

    def advance(self):
        """One step. Returns False when there is nothing left to do."""
        if self.finished:
            return False
        ops = next(self._gen, None)
        if ops is None:
            self.finished = True
            return False
        changed = self._apply(ops)
        self.steps_done += 1
        self._quiet = 0 if changed else self._quiet + 1
        if self.quiet_limit and self._quiet >= self.quiet_limit:
            self.finished = True
        return True

    def _apply(self, ops):
        C, ck, act = self.C, self.ck, self.activity
        changed = 0
        for a, b, asc in ops:
            A = C[:, a].copy()
            B = C[:, b].copy()
            KA, KB = ck[A], ck[B]
            swap = np.where(asc, KA > KB, KA < KB)
            if not swap.any():
                continue
            C[:, a] = np.where(swap, B, A)
            C[:, b] = np.where(swap, A, B)
            act[:, a] += swap
            act[:, b] += swap
            changed += int(np.count_nonzero(swap))
        return changed

    def render(self):
        out = self.flat.copy()
        v = self.valid
        out[self.slot[v]] = self.flat[self.C[v]]
        return out.reshape(self.shape)

    def activity_map(self):
        H, W = self.shape[:2]
        heat = np.zeros(H * W, np.float32)
        heat[self.slot[self.valid]] = self.activity[self.valid]
        return heat.reshape(H, W)


def record(rgb, angle, prop, levels, reverse, algo, region=None, lo=0.0, hi=1.0, max_frames=120):
    """Animation frames from the current state, for the GIF export."""
    region = effective_region(rgb, region, prop, lo, hi)
    segs = build_segments(rgb.shape[:2], angle, region)
    ck = sort_keys(rgb.reshape(-1, 3), prop, levels, reverse)
    anim = Animator(rgb, segs, ck, algo)
    every = max(1, anim.total // max_frames)
    frames = [anim.render()]
    k = 0
    while anim.advance():
        k += 1
        if k % every == 0:
            frames.append(anim.render())
    frames.append(anim.render())
    return frames
