from PIL import Image
from server.postprocess import (
    crop_to_subject, downscale, extract_palette, fit_into, sprite_palette,
    snap_to_palette, subject_palette, remove_background,
)


def test_mirror_symmetry_even_width():
    from server.postprocess import mirror_symmetry
    img = Image.new("RGBA", (4, 1), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.putpixel((1, 0), (0, 255, 0, 255))
    out = mirror_symmetry(img)
    assert out.getpixel((3, 0)) == (255, 0, 0, 255)  # mirrored from x=0
    assert out.getpixel((2, 0)) == (0, 255, 0, 255)  # mirrored from x=1


def test_mirror_symmetry_odd_width_keeps_center():
    from server.postprocess import mirror_symmetry
    img = Image.new("RGBA", (3, 1), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.putpixel((1, 0), (1, 2, 3, 255))  # center column
    out = mirror_symmetry(img)
    assert out.getpixel((1, 0)) == (1, 2, 3, 255)
    assert out.getpixel((2, 0)) == (255, 0, 0, 255)


def test_crop_to_subject_bounds():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(40, 60):
        for y in range(20, 80):
            img.putpixel((x, y), (255, 0, 0, 255))
    out = crop_to_subject(img, margin=0)
    assert out.size == (20, 60)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)


def test_fit_into_letterboxes_preserving_aspect():
    img = Image.new("RGBA", (10, 20), (0, 255, 0, 255))  # tall subject
    out = fit_into(img, (16, 16))
    assert out.size == (16, 16)
    assert out.getpixel((8, 8))[3] == 255      # subject in the center
    assert out.getpixel((0, 8))[3] == 0        # transparent letterbox sides


def test_downscale_transparent_majority_cell_stays_transparent():
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0, 255))  # 1/16 opaque, below keep=0.3
    out = downscale(img, (1, 1))
    assert out.getpixel((0, 0))[3] == 0


def test_downscale_keeps_thin_opaque_features():
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    for x in range(4):
        img.putpixel((x, 0), (255, 0, 0, 255))
        img.putpixel((x, 1), (255, 0, 0, 255))  # 8/16 = 50% opaque
    out = downscale(img, (1, 1))
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)


def test_subject_palette_ignores_transparent_pixels():
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    img.putpixel((0, 0), (200, 10, 10, 255))
    img.putpixel((1, 0), (10, 200, 10, 255))
    pal = subject_palette(img, max_colors=4)
    assert (200, 10, 10) in pal and (10, 200, 10) in pal


def test_downscale_uniform_blocks():
    # 4x4 image made of four 2x2 solid blocks -> 2x2 image of those colors
    img = Image.new("RGBA", (4, 4))
    colors = {(0, 0): (255, 0, 0, 255), (2, 0): (0, 255, 0, 255),
              (0, 2): (0, 0, 255, 255), (2, 2): (255, 255, 0, 255)}
    for (bx, by), c in colors.items():
        for dx in range(2):
            for dy in range(2):
                img.putpixel((bx + dx, by + dy), c)
    out = downscale(img, (2, 2))
    assert out.size == (2, 2)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)
    assert out.getpixel((1, 0)) == (0, 255, 0, 255)
    assert out.getpixel((0, 1)) == (0, 0, 255, 255)
    assert out.getpixel((1, 1)) == (255, 255, 0, 255)


def test_downscale_picks_dominant_color():
    # 4x4 single cell: 13 red pixels, 3 green -> red wins
    img = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    for p in [(0, 0), (1, 0), (2, 0)]:
        img.putpixel(p, (0, 255, 0, 255))
    out = downscale(img, (1, 1))
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)


def test_downscale_non_integer_ratio():
    # 10x10 -> 3x3 must not crash and keep size
    img = Image.new("RGBA", (10, 10), (7, 7, 7, 255))
    out = downscale(img, (3, 3))
    assert out.size == (3, 3)
    assert out.getpixel((1, 1)) == (7, 7, 7, 255)


def test_snap_to_palette():
    img = Image.new("RGBA", (2, 1))
    img.putpixel((0, 0), (250, 10, 10, 255))   # near red
    img.putpixel((1, 0), (10, 10, 250, 0))     # near blue, transparent
    out = snap_to_palette(img, [(255, 0, 0), (0, 0, 255)])
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)
    assert out.getpixel((1, 0))[3] == 0  # alpha preserved


def test_sprite_palette_extracts_opaque_colors():
    img = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    img.putpixel((0, 0), (1, 2, 3, 255))
    img.putpixel((1, 0), (4, 5, 6, 255))
    pal = sprite_palette(img)
    assert set(pal) == {(1, 2, 3), (4, 5, 6)}


def test_sprite_palette_none_when_too_many():
    img = Image.new("RGBA", (16, 16))
    for y in range(16):
        for x in range(16):
            img.putpixel((x, y), (x * 16, y * 16, 0, 255))
    assert sprite_palette(img, limit=64) is None


def test_extract_palette_limits_colors():
    img = Image.new("RGBA", (8, 8), (200, 30, 30, 255))
    for x in range(8):
        img.putpixel((x, 0), (20, 200, 20, 255))
    pal = extract_palette(img, max_colors=2)
    assert len(pal) <= 2


def test_remove_background():
    # white bg, red 2x2 square in the middle; bg becomes transparent,
    # square stays opaque
    img = Image.new("RGBA", (6, 6), (255, 255, 255, 255))
    for x in range(2, 4):
        for y in range(2, 4):
            img.putpixel((x, y), (255, 0, 0, 255))
    out = remove_background(img)
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((2, 2)) == (255, 0, 0, 255)


def test_remove_background_keeps_inner_holes():
    # ring of red on white bg: outside cleared, enclosed white center kept
    img = Image.new("RGBA", (5, 5), (255, 255, 255, 255))
    for x in range(1, 4):
        for y in range(1, 4):
            img.putpixel((x, y), (255, 0, 0, 255))
    img.putpixel((2, 2), (255, 255, 255, 255))
    out = remove_background(img)
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((2, 2))[3] == 255  # enclosed, not reached by flood


def _reference_remove_bg(img, tolerance=12):
    """Original per-pixel BFS flood, kept only to pin the vectorized version."""
    import numpy as np
    from collections import deque
    arr = np.asarray(img.convert("RGBA")).astype(int).copy()
    h, w = arr.shape[:2]
    corners = np.array([arr[0, 0, :3], arr[0, w - 1, :3],
                        arr[h - 1, 0, :3], arr[h - 1, w - 1, :3]])
    if corners.std(axis=0).max() > tolerance:
        return img.convert("RGBA")
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
            if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx] \
                    and is_bg(ny, nx):
                seen[ny, nx] = True
                queue.append((ny, nx))
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def test_remove_background_matches_reference_flood():
    import numpy as np
    # Concave-background + enclosed pocket: a red C-shape on white so the
    # flood must wrap around, plus a white pocket sealed inside the arms.
    img = Image.new("RGBA", (9, 9), (255, 255, 255, 255))
    red = (255, 0, 0, 255)
    for x in range(2, 7):
        img.putpixel((x, 2), red)
        img.putpixel((x, 6), red)
    for y in range(2, 7):
        img.putpixel((2, y), red)
    for y in range(3, 6):        # right side open (the C mouth)
        img.putpixel((6, y), red) if y == 4 else None
    for x in range(3, 6):        # seal an inner white pocket
        img.putpixel((x, 3), red)
        img.putpixel((x, 5), red)
    img.putpixel((3, 4), red)
    img.putpixel((5, 4), red)
    ref = np.asarray(_reference_remove_bg(img, 12))
    got = np.asarray(remove_background(img, 12))
    assert np.array_equal(got, ref)
