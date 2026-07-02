"""Turn AI output into actual pixel art: downscale, palette, transparency."""
import numpy as np
from PIL import Image
from collections import deque


def downscale(img: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Dominant-color downscale. Each output pixel is the most frequent
    RGBA value inside its source cell (nearest-style cell boundaries)."""
    tw, th = target_size
    arr = np.asarray(img.convert("RGBA"))
    h, w = arr.shape[:2]
    xs = np.linspace(0, w, tw + 1, dtype=int)
    ys = np.linspace(0, h, th + 1, dtype=int)
    out = np.zeros((th, tw, 4), dtype=np.uint8)
    for j in range(th):
        for i in range(tw):
            cell = arr[ys[j]:max(ys[j + 1], ys[j] + 1),
                       xs[i]:max(xs[i + 1], xs[i] + 1)].reshape(-1, 4)
            colors, counts = np.unique(cell, axis=0, return_counts=True)
            out[j, i] = colors[counts.argmax()]
    return Image.fromarray(out, "RGBA")


def extract_palette(img: Image.Image, max_colors: int = 16) -> list[tuple[int, int, int]]:
    """Median-cut palette of an AI-generated image."""
    q = img.convert("RGB").quantize(colors=max_colors)
    raw = q.getpalette()[: max_colors * 3]
    used = sorted(set(q.getdata()))
    return [tuple(raw[i * 3: i * 3 + 3]) for i in used]


def sprite_palette(img: Image.Image, limit: int = 64) -> list[tuple[int, int, int]] | None:
    """Unique opaque colors of a hand-made sprite; None if not palette-like."""
    arr = np.asarray(img.convert("RGBA")).reshape(-1, 4)
    opaque = arr[arr[:, 3] > 0][:, :3]
    colors = np.unique(opaque, axis=0)
    if len(colors) == 0 or len(colors) > limit:
        return None
    return [tuple(int(v) for v in c) for c in colors]


def snap_to_palette(img: Image.Image, palette: list[tuple[int, int, int]]) -> Image.Image:
    """Snap every pixel's RGB to the nearest palette color; keep alpha."""
    arr = np.asarray(img.convert("RGBA")).astype(int)
    h, w = arr.shape[:2]
    flat = arr.reshape(-1, 4)
    pal = np.array(palette, dtype=int)  # (P, 3)
    dists = ((flat[:, None, :3] - pal[None, :, :]) ** 2).sum(axis=2)  # (N, P)
    snapped = pal[dists.argmin(axis=1)]
    out = np.concatenate([snapped, flat[:, 3:4]], axis=1).astype(np.uint8)
    return Image.fromarray(out.reshape(h, w, 4), "RGBA")


def remove_background(img: Image.Image, tolerance: int = 12) -> Image.Image:
    """Flood-fill from all border pixels that match the corner-average
    background color (within tolerance); reached pixels become transparent.
    Enclosed same-colored regions are NOT cleared (flood, not global match)."""
    arr = np.asarray(img.convert("RGBA")).astype(int).copy()
    h, w = arr.shape[:2]
    corners = np.array([arr[0, 0, :3], arr[0, w - 1, :3],
                        arr[h - 1, 0, :3], arr[h - 1, w - 1, :3]])
    if corners.std(axis=0).max() > tolerance:
        return img.convert("RGBA")  # no uniform background detected
    bg = corners.mean(axis=0)

    def is_bg(y, x):
        return np.abs(arr[y, x, :3] - bg).max() <= tolerance

    seen = np.zeros((h, w), dtype=bool)
    queue = deque()
    for x in range(w):
        for y in (0, h - 1):
            if is_bg(y, x) and not seen[y, x]:
                seen[y, x] = True
                queue.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if is_bg(y, x) and not seen[y, x]:
                seen[y, x] = True
                queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        arr[y, x, 3] = 0
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx] and is_bg(ny, nx):
                seen[ny, nx] = True
                queue.append((ny, nx))
    return Image.fromarray(arr.astype(np.uint8), "RGBA")
