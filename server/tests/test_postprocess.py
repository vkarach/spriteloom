from PIL import Image
from server.postprocess import (
    downscale, extract_palette, sprite_palette, snap_to_palette, remove_background,
)


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
