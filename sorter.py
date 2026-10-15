"""Sortowanie pikseli wzdłuż wektora kierunku. Obraz nigdy nie jest obracany.

Piksele grupujemy w linie równoległe do kierunku i każdą linię sortujemy wzdłuż niej.
Kluczem jest wybrana cecha (jasność, odcień, nasycenie, wartość), opcjonalnie
skwantowana. Każdy piksel dostaje unikalny klucz (najpierw cecha, potem numer piksela),
więc animacja i szybki wynik zawsze dają identyczny porządek.
"""
import math

import numpy as np

PROPERTIES = (("luma", "Jasność"), ("hue", "Odcień"), ("sat", "Nasycenie"), ("val", "Wartość"))
ALGORITHMS = (("odd_even", "Odd-even"), ("bitonic", "Bitoniczny"), ("bubble", "Bąbelkowy"))
QUIET_LIMIT = {"odd_even": 2, "bubble": 1, "bitonic": None}
LUM = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def direction(angle):
    """Kąt w stopniach -> wektor (dx, dy). 0° = w prawo, 90° = w górę, 45° = w prawy górny róg."""
    rad = math.radians(angle)
    dx, dy = math.cos(rad), -math.sin(rad)   # oś y obrazu rośnie w dół
    m = max(abs(dx), abs(dy))
    return dx / m, dy / m


def build_segments(shape, angle, region=None):
    """Odcinki (tablice indeksów pikseli), każdy ciągły i uporządkowany wzdłuż kierunku.

    region: bool (H, W), True = wolno sortować; None = cały obraz.
    """
    H, W = shape
    dx, dy = direction(angle)
    xs = np.tile(np.arange(W, dtype=np.float32), H)
    ys = np.repeat(np.arange(H, dtype=np.float32), W)
    line = np.round(-xs * dy + ys * dx).astype(np.int32)   # numer linii równoległej do kierunku
    pos = xs * dx + ys * dy                                  # położenie wzdłuż kierunku
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
    """Cecha każdego piksela, znormalizowana do przedziału [0, 1]."""
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
    """Unikalny klucz każdego piksela: cecha (opcjonalnie skwantowana), potem numer piksela."""
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


def sort_fast(flat, segments, ck):
    """Sortuje każdy odcinek naraz, bez animacji."""
    out = flat.copy()
    for seg in segments:
        out[seg] = flat[seg[np.argsort(ck[seg])]]
    return out


def run_fast(rgb, angle, prop, levels, reverse, region=None):
    """Pełny przebieg bez animacji. Służy do wyniku w pełnej rozdzielczości."""
    flat = rgb.reshape(-1, 3)
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
    """Generator kroków. Każdy krok to lista operacji porównaj-zamień (a, b, kierunek)."""
    if algo == "odd_even":
        for p in range(P):
            a = np.arange(p % 2, P - 1, 2)
            yield [(a, a + 1, True)] if a.size else []
    elif algo == "bubble":
        for p in range(P - 1):
            yield [(j, j + 1, True) for j in range(P - 1 - p)]
    else:  # bitoniczny: sieć o O(log² n) etapach
        idx = np.arange(P)
        k = 2
        while k <= P:
            j = k // 2
            while j >= 1:
                partner = idx ^ j
                keep = partner > idx
                a, b = idx[keep], partner[keep]
                yield [(a, b, (a & k) == 0)]
                j //= 2
            k *= 2


class Animator:
    """Wykonuje wybrany algorytm krok po kroku, na wszystkich odcinkach naraz."""

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
        # slot[i, j]: który piksel fizycznie leży na pozycji j odcinka i (-1 = brak)
        self.slot = np.full((n, P), -1, np.int64)
        for i, seg in enumerate(segments):
            self.slot[i, :seg.size] = seg
        self.valid = self.slot >= 0
        self.C = np.where(self.valid, self.slot, N)      # który piksel jest na pozycji (N = pusta)
        self.ck = np.append(ck, np.inf)                  # pusta pozycja zawsze na końcu
        self.activity = np.zeros((n, P), np.float32)

    def advance(self):
        """Jeden krok. Zwraca False, gdy nie ma już nic do zrobienia."""
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


def record(rgb, angle, prop, levels, reverse, algo, region=None, max_frames=120):
    """Klatki animacji od bieżącego stanu, do zapisu GIF."""
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