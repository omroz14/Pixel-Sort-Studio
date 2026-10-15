import numpy as np
from PIL import Image

def build_lines(h, w, dx, dy):
    """Grupuje piksele w linie prostopadłe do (dx, dy), każdą uporządkowaną wzdłuż wektora."""
    ys, xs = np.mgrid[0:h, 0:w]
    t = np.round(xs * dx + ys * dy, 3).ravel()   # numer linii
    s = (-xs * dy + ys * dx).ravel()             # pozycja wzdłuż linii
    order = np.lexsort((s, t))                   # najpierw po t, potem po s
    bounds = np.flatnonzero(np.diff(t[order])) + 1
    return np.split(order, bounds)

def sort_frames(arr, direction, quant=None, every=5, max_passes=None):
    """
    Zwraca listę klatek (np.ndarray) pokazujących stopniowe sortowanie.
    every: zapisuj klatkę co tyle przejść
    """
    h, w = arr.shape[:2]
    flat = arr.reshape(-1, arr.shape[2])
    lum = flat[:, :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    if quant:
        lum = np.floor(lum / 256 * quant)

    lines = build_lines(h, w, *direction)
    L, M = len(lines), max(len(l) for l in lines)

    # slot: stałe pozycje pikseli na liniach (-1 = wypełnienie)
    slot = np.full((L, M), -1, dtype=np.int64)
    for i, l in enumerate(lines):
        slot[i, :len(l)] = l
    valid = slot >= 0

    # C: który piksel źródłowy aktualnie leży w danym slocie; K: jego klucz (jasność)
    C = slot.copy()
    K = np.full((L, M), np.inf, dtype=np.float32)
    K[valid] = lum[slot[valid]]

    def render(C):
        out = flat.copy()
        out[slot[valid]] = flat[C[valid]]
        return out.reshape(arr.shape)

    frames = [render(C)]
    passes = M if max_passes is None else max_passes
    for p in range(passes):
        left = np.arange(p % 2, M - 1, 2)
        right = left + 1
        if left.size == 0:
            break
        swap = K[:, left] > K[:, right]

        Cl, Cr = C[:, left].copy(), C[:, right].copy()
        Kl, Kr = K[:, left].copy(), K[:, right].copy()
        C[:, left] = np.where(swap, Cr, Cl)
        C[:, right] = np.where(swap, Cl, Cr)
        K[:, left] = np.where(swap, Kr, Kl)
        K[:, right] = np.where(swap, Kl, Kr)

        if (p + 1) % every == 0:
            frames.append(render(C))

    frames.append(render(C))
    return frames

def save_gif(frames, path="sort.gif", ms=60):
    imgs = [Image.fromarray(f) for f in frames]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=ms, loop=0)

def diff_map(frames):
    """Mapa różnic: ile razy i jak mocno zmieniał się każdy piksel między klatkami."""
    total = np.zeros(frames[0].shape[:2], np.float32)
    for a, b in zip(frames[:-1], frames[1:]):
        total += np.abs(b.astype(np.float32) - a.astype(np.float32)).sum(axis=2)
    total /= total.max() + 1e-9
    return Image.fromarray((total * 255).astype(np.uint8))