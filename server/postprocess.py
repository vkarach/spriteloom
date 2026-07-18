"""Turn AI output into actual pixel art: downscale, palette, transparency."""
import numpy as np
from PIL import Image
from scipy import ndimage


def downscale(img: Image.Image, target_size: tuple[int, int],
              keep: float = 0.3,
              palette: list[tuple[int, int, int]] | None = None,
              dark_share: float = 0.15, dark_gap: int = 35
              ) -> Image.Image:
    """Palette-majority downscale: each opaque pixel votes for its nearest
    palette color, the cell takes the winner. Unlike a median this never
    invents in-between colors, so outlines survive. A cell stays opaque
    when at least `keep` of its pixels are opaque.

    Outline preservation: a palette color at least `dark_gap` luminance
    darker than the majority winner takes the cell with only a `dark_share`
    minority - thin dark lines land as split minorities across neighboring
    cells and would otherwise vanish into speckles."""
    tw, th = target_size
    arr = np.asarray(img.convert("RGBA"))
    h, w = arr.shape[:2]
    if palette is None:
        palette = subject_palette(img, 16)
    pal = np.asarray(palette, dtype=np.float32)

    flat = arr.reshape(-1, 4)
    rgb = flat[:, :3].astype(np.float32)
    dists = ((rgb ** 2).sum(1)[:, None] - 2.0 * (rgb @ pal.T)
             + (pal ** 2).sum(1)[None, :])
    nearest = dists.argmin(1)

    ci = np.minimum(np.arange(w) * tw // w, tw - 1)
    cj = np.minimum(np.arange(h) * th // h, th - 1)
    cell = (cj[:, None] * tw + ci[None, :]).ravel()
    opaque = flat[:, 3] > 0
    votes = np.zeros((tw * th, len(pal)), dtype=np.int32)
    np.add.at(votes, (cell[opaque], nearest[opaque]), 1)
    total = np.bincount(cell, minlength=tw * th)
    opq = votes.sum(1)

    winner = votes.argmax(1)
    lum = pal @ np.float32([0.299, 0.587, 0.114])
    need = np.maximum(1, (dark_share * opq).astype(np.int32))
    cand = (votes >= need[:, None]) \
        & (lum[None, :] <= lum[winner][:, None] - dark_gap)
    dark = np.where(cand, lum[None, :], np.inf).argmin(1)
    winner = np.where(cand.any(1), dark, winner)

    out = np.zeros((tw * th, 4), dtype=np.uint8)
    solid = (opq > 0) & (opq >= keep * total)
    out[solid, :3] = pal[winner[solid]].astype(np.uint8)
    out[solid, 3] = 255
    return Image.fromarray(out.reshape(th, tw, 4), "RGBA")


def extract_palette(img: Image.Image, max_colors: int = 16) -> list[tuple[int, int, int]]:
    """Median-cut palette of an AI-generated image."""
    q = img.convert("RGB").quantize(colors=max_colors)
    raw = q.getpalette()[: max_colors * 3]
    used = sorted(set(q.getdata()))
    return [tuple(raw[i * 3: i * 3 + 3]) for i in used]


def mirror_symmetry(img: Image.Image) -> Image.Image:
    """Mirror the left half onto the right (center column kept for odd
    widths). For front/back views this guarantees symmetric features -
    a standard pixel-art technique."""
    arr = np.asarray(img.convert("RGBA")).copy()
    h, w = arr.shape[:2]
    half = w // 2
    arr[:, w - half:] = arr[:, :half][:, ::-1]
    return Image.fromarray(arr, "RGBA")


def crop_to_subject(img: Image.Image, margin: float = 0.04) -> Image.Image:
    """Crop a background-removed image to its opaque bounding box (plus a
    small margin) so the subject, not the empty canvas, gets the pixels
    after downscaling."""
    arr = np.asarray(img.convert("RGBA"))
    ys, xs = np.nonzero(arr[:, :, 3])
    if len(xs) == 0:
        return img.convert("RGBA")
    pad = int(margin * max(img.width, img.height))
    x0 = max(int(xs.min()) - pad, 0)
    x1 = min(int(xs.max()) + 1 + pad, img.width)
    y0 = max(int(ys.min()) - pad, 0)
    y1 = min(int(ys.max()) + 1 + pad, img.height)
    return img.convert("RGBA").crop((x0, y0, x1, y1))


def fit_into(img: Image.Image, target_size: tuple[int, int],
             palette: list[tuple[int, int, int]] | None = None
             ) -> Image.Image:
    """Downscale preserving aspect ratio and center on a transparent canvas
    of target_size (pixel-art letterboxing)."""
    tw, th = target_size
    scale = min(tw / img.width, th / img.height)
    fw = max(1, round(img.width * scale))
    fh = max(1, round(img.height * scale))
    small = downscale(img, (fw, fh), palette=palette)
    canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    canvas.paste(small, ((tw - fw) // 2, (th - fh) // 2))
    return canvas


def subject_palette(img: Image.Image, max_colors: int = 16) -> list[tuple[int, int, int]]:
    """Median-cut palette from OPAQUE pixels only. On background-removed
    images this spends every palette slot on the subject instead of wasting
    most of them on background shades."""
    arr = np.asarray(img.convert("RGBA"))
    opaque = arr[arr[:, :, 3] > 0][:, :3]
    if len(opaque) == 0:
        return [(0, 0, 0)]
    strip = Image.fromarray(opaque.reshape(1, -1, 3), "RGB")
    q = strip.quantize(colors=max_colors)
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
    pal = np.array(palette, dtype=int)
    dists = ((flat[:, None, :3] - pal[None, :, :]) ** 2).sum(axis=2)
    snapped = pal[dists.argmin(axis=1)]
    out = np.concatenate([snapped, flat[:, 3:4]], axis=1).astype(np.uint8)
    out[out[:, 3] == 0] = 0  # fully transparent pixels carry no stray color
    return Image.fromarray(out.reshape(h, w, 4), "RGBA")


def remove_background(img: Image.Image, tolerance: int = 12,
                      force: bool = False, step_tol: int = 10
                      ) -> Image.Image:
    """Flood-fill from border pixels matching the dominant border color;
    reached pixels become transparent, enclosed regions are kept.

    A second pass grows the cleared region through smooth gradients (drop
    shadows, wall vignettes): a pixel is eaten when it differs from an
    already-cleared neighbor by at most `step_tol` per channel and stays
    within 5x tolerance of the background color. Sharp subject outlines
    stop the growth. Reverted when under 60% of the border ends up cleared
    (no dominant background), unless `force` is set."""
    arr = np.asarray(img.convert("RGBA")).astype(int).copy()
    h, w = arr.shape[:2]
    border = np.concatenate([arr[0, :, :3], arr[-1, :, :3],
                             arr[:, 0, :3], arr[:, -1, :3]])
    # snap the median to a real border color (a 50/50 border would otherwise
    # yield a blend that matches nothing)
    med = np.median(border, axis=0)
    bg = border[np.abs(border - med).sum(axis=1).argmin()]

    bgmask = np.abs(arr[:, :, :3] - bg).max(axis=2) <= tolerance
    seed = np.zeros((h, w), dtype=bool)
    seed[0, :], seed[-1, :] = bgmask[0, :], bgmask[-1, :]
    seed[:, 0], seed[:, -1] = bgmask[:, 0], bgmask[:, -1]
    cross = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
    cleared = ndimage.binary_propagation(seed, mask=bgmask, structure=cross)

    rgb = arr[:, :, :3]
    near_bg = np.abs(rgb - bg).max(axis=2) <= 5 * tolerance
    # per-direction smooth steps, precomputed once: smooth[k][y, x] is True
    # when pixel (y, x) is a gentle continuation of its neighbor opposite
    # to shift k
    shifts = ((1, 0), (-1, 0), (0, 1), (0, -1))
    smooth = []
    for dy, dx in shifts:
        ok = np.zeros((h, w), dtype=bool)
        dst = (slice(max(dy, 0), h + min(dy, 0)),
               slice(max(dx, 0), w + min(dx, 0)))
        src = (slice(max(-dy, 0), h + min(-dy, 0)),
               slice(max(-dx, 0), w + min(-dx, 0)))
        ok[dst] = np.abs(rgb[dst] - rgb[src]).max(axis=2) <= step_tol
        smooth.append((dst, src, ok & near_bg))
    for _ in range(max(h, w)):
        grew = False
        for dst, src, ok in smooth:
            cand = np.zeros((h, w), dtype=bool)
            cand[dst] = cleared[src]
            cand &= ok & ~cleared
            if cand.any():
                cleared |= cand
                grew = True
        if not grew:
            break

    edge = np.concatenate([cleared[0, :], cleared[-1, :],
                           cleared[:, 0], cleared[:, -1]])
    if not force and edge.mean() < 0.6:
        return img.convert("RGBA")  # no dominant background color detected
    arr[cleared, 3] = 0

    # debris pass: leftover scraps pinned to the border (floor contact
    # lines, shadow slivers) are background, not subject. Two kinds:
    # small isolated components, and thin appendages of the silhouette
    # (opening strips anything under ~5px thick) that run into the border.
    # shadings of the background (same chromaticity, darker/lighter - floor
    # shadows, wall vignettes): reachable ones get eaten by the flood below;
    # enclosed pockets and anything behind an outline stay
    rgbf = arr[:, :, :3].astype(np.float64)
    bgf = bg.astype(np.float64)
    s = (rgbf @ bgf) / (bgf @ bgf)
    shade = (np.abs(rgbf - s[..., None] * bgf).max(axis=2) <= tolerance) \
        & (s >= 0.25) & (s <= 1.15)

    k = 5 if min(h, w) >= 128 else 3
    for _ in range(2):  # dropping a strip can expose/isolate more debris
        opaque = arr[:, :, 3] > 0
        gone = ~opaque
        reach = ndimage.binary_propagation(gone, mask=gone | shade,
                                           structure=cross)
        arr[reach & opaque, 3] = 0
        opaque = arr[:, :, 3] > 0
        thin = opaque & ~ndimage.binary_opening(opaque,
                                                structure=np.ones((k, k)))
        for mask, cap in ((opaque, 0.02 * opaque.sum()), (thin, np.inf)):
            labels, n = ndimage.label(mask, structure=cross)
            if n < (2 if mask is opaque else 1):
                continue  # a lone component is the subject, never debris
            sizes = np.bincount(labels.ravel())
            on_border = np.zeros(n + 1, dtype=bool)
            for band in (labels[0, :], labels[-1, :],
                         labels[:, 0], labels[:, -1]):
                on_border[band] = True
            drop = on_border & (sizes < cap)
            drop[0] = False
            arr[drop[labels], 3] = 0
    return Image.fromarray(arr.astype(np.uint8), "RGBA")
