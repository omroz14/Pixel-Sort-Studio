"""Mask brush: stroke drawing and the preview overlay."""
import numpy as np
from PIL import Image, ImageDraw


def stroke(mask, p0, p1, radius, value):
    """Stroke from p0 to p1. Circles at the ends leave no gaps during fast movement."""
    d = ImageDraw.Draw(mask)
    r = max(1, round(radius))
    a = (round(p0[0]), round(p0[1]))
    b = (round(p1[0]), round(p1[1]))
    if a != b:
        d.line([a, b], fill=value, width=2 * r)
    for x, y in (a, b):
        d.ellipse((x - r, y - r, x + r, y + r), fill=value)


def downscale(mask, size):
    """Shrinks the mask without losing thin lines: every touched pixel stays selected."""
    return mask.resize(size, Image.BOX).point(lambda v: 255 if v else 0)


def overlay(base, inside, color, alpha=0.5):
    """Tints the mask area. base: HxWx3 uint8, inside: HxW bool, color: RGB tuple."""
    out = base.astype(np.float32)
    tint = np.array(color, np.float32)
    out[inside] = out[inside] * (1 - alpha) + tint * alpha
    return out.astype(np.uint8)
