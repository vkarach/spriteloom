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


def test_downscale_with_palette_keeps_thin_outline_colors():
    # 34 outline px vs 30 fill px: majority picks outline, median would blend
    img = Image.new("RGBA", (8, 8), (200, 60, 60, 255))
    n = 0
    for y in range(8):
        for x in range(8):
            if n < 34:
                img.putpixel((x, y), (20, 10, 10, 255))
                n += 1
    out = downscale(img, (1, 1), palette=[(20, 10, 10), (200, 60, 60)])
    assert out.getpixel((0, 0)) == (20, 10, 10, 255)


def test_downscale_output_only_uses_palette_colors():
    # noisy shades of red -> output must be an exact palette color
    img = Image.new("RGBA", (4, 4))
    for y in range(4):
        for x in range(4):
            img.putpixel((x, y), (200 + x + y * 4, 8, 8, 255))
    out = downscale(img, (1, 1), palette=[(210, 8, 8), (0, 0, 255)])
    assert out.getpixel((0, 0)) == (210, 8, 8, 255)


def test_fit_into_accepts_palette():
    img = Image.new("RGBA", (10, 20), (100, 200, 100, 255))
    out = fit_into(img, (16, 16), palette=[(0, 255, 0)])
    assert out.getpixel((8, 8)) == (0, 255, 0, 255)


def test_remove_background_tolerates_one_odd_corner():
    # subject touches a corner; the old 4-corner check bailed out here
    img = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
    for x in range(3):
        for y in range(3):
            img.putpixel((x, y), (255, 0, 0, 255))
    out = remove_background(img)
    assert out.getpixel((9, 9))[3] == 0
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)


def test_remove_background_skips_when_border_is_busy():
    # half red / half blue border: no dominant background, keep everything
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    for y in range(5, 10):
        for x in range(10):
            img.putpixel((x, y), (0, 0, 255, 255))
    out = remove_background(img)
    arr = out.load()
    assert all(arr[x, y][3] == 255 for x in range(10) for y in range(10))


def test_remove_background_force_overrides_detection():
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    for y in range(5, 10):
        for x in range(10):
            img.putpixel((x, y), (0, 0, 255, 255))
    out = remove_background(img, force=True)
    assert (out.getpixel((0, 0))[3] == 0) or (out.getpixel((9, 9))[3] == 0)


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


def test_remove_background_eats_soft_shadow():
    # white bg with a smooth gray gradient (drop shadow) next to a sharp
    # dark subject: shadow goes, subject stays
    img = Image.new("RGBA", (40, 20), (250, 250, 250, 255))
    for i, x in enumerate(range(20, 30)):    # gradient 250 -> 205, step 5
        v = 250 - (i + 1) * 5
        for y in range(20):
            img.putpixel((x, y), (v, v, v, 255))
    for x in range(5, 15):                   # sharp-edged dark subject
        for y in range(5, 15):
            img.putpixel((x, y), (90, 50, 20, 255))
    out = remove_background(img, tolerance=12)
    assert out.getpixel((29, 10))[3] == 0    # darkest shadow band cleared
    assert out.getpixel((10, 10)) == (90, 50, 20, 255)


def test_remove_background_handles_gradient_lit_wall():
    # lighting gradient across the whole bg: no single color covers 60% of
    # the border, but smooth growth still clears it all
    img = Image.new("RGBA", (60, 20), (0, 0, 0, 0))
    for x in range(60):
        v = 150 + x  # 150..209, far beyond tolerance end to end
        for y in range(20):
            img.putpixel((x, y), (v, v, v, 255))
    for x in range(25, 35):
        for y in range(5, 15):
            img.putpixel((x, y), (30, 30, 200, 255))
    out = remove_background(img, tolerance=12)
    assert out.getpixel((0, 10))[3] == 0
    assert out.getpixel((59, 10))[3] == 0
    assert out.getpixel((30, 10)) == (30, 30, 200, 255)


def test_remove_background_drops_border_debris():
    # a thin dark floor line pinned to the bottom border survives the flood
    # (sharp edge) but must go as debris; the big subject stays
    img = Image.new("RGBA", (100, 100), (250, 250, 250, 255))
    for x in range(10, 90):
        for y in range(10, 90):
            img.putpixel((x, y), (90, 50, 20, 255))
    for x in range(100):
        img.putpixel((x, 99), (40, 30, 25, 255))
    out = remove_background(img, tolerance=12)
    assert out.getpixel((50, 99))[3] == 0    # floor line dropped
    assert out.getpixel((50, 50)) == (90, 50, 20, 255)


def test_remove_background_eats_bg_shading_touching_cleared_area():
    # a gray patch = darkened white bg (same chromaticity) glued to the
    # subject and open to the cleared bg: it is a cast shadow, remove it
    img = Image.new("RGBA", (40, 40), (250, 250, 250, 255))
    for x in range(10, 30):
        for y in range(10, 25):
            img.putpixel((x, y), (200, 60, 40, 255))
    for x in range(10, 30):
        for y in range(25, 30):
            img.putpixel((x, y), (125, 125, 125, 255))
    out = remove_background(img, tolerance=12)
    assert out.getpixel((20, 27))[3] == 0     # shadow gone
    assert out.getpixel((20, 15)) == (200, 60, 40, 255)


def test_downscale_dark_minority_outline_wins_cell():
    # 8x8 cell: 12/64 dark outline pixels (~19%) - majority vote alone
    # would drop the line, the dark bias keeps it
    img = Image.new("RGBA", (8, 8), (200, 150, 100, 255))
    for x in range(8):
        img.putpixel((x, 0), (20, 12, 7, 255))
    for x in range(4):
        img.putpixel((x, 1), (20, 12, 7, 255))
    out = downscale(img, (1, 1), palette=[(20, 12, 7), (200, 150, 100)])
    assert out.getpixel((0, 0)) == (20, 12, 7, 255)


def test_downscale_dark_bias_ignores_stray_noise():
    # a single dark pixel in a 8x8 cell (1.5%) must NOT take the cell
    img = Image.new("RGBA", (8, 8), (200, 150, 100, 255))
    img.putpixel((3, 3), (20, 12, 7, 255))
    out = downscale(img, (1, 1), palette=[(20, 12, 7), (200, 150, 100)])
    assert out.getpixel((0, 0)) == (200, 150, 100, 255)


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
