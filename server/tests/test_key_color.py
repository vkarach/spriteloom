import numpy as np
from PIL import Image

from server.instruct import KleinPipeline, flatten_to_key
from server.postprocess import key_color, remove_background


def _sprite_with_black_outline():
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for x in range(4, 12):
        for y in range(4, 12):
            img.putpixel((x, y), (140, 70, 40, 255))
    for i in range(4, 12):  # outline in the same black the old padding used
        for x, y in ((i, 4), (i, 11), (4, i), (11, i)):
            img.putpixel((x, y), (0, 0, 0, 255))
    return img


def test_key_color_avoids_the_sprite_colors():
    img = _sprite_with_black_outline()
    key = key_color(img)
    used = np.asarray(img.convert("RGBA")).reshape(-1, 4)
    used = used[used[:, 3] > 0][:, :3].astype(int)
    assert np.abs(used - np.array(key)).max(axis=1).min() > 100


def test_key_color_dodges_a_magenta_sprite():
    img = Image.new("RGBA", (4, 4), (255, 0, 255, 255))
    assert key_color(img) != (255, 0, 255)


def test_flatten_to_key_fills_transparent_pixels():
    img = _sprite_with_black_outline()
    flat, key = flatten_to_key(img)
    assert flat.mode == "RGB"
    assert flat.getpixel((0, 0)) == key
    assert flat.getpixel((6, 6)) == (140, 70, 40)


def test_prep_input_pads_with_the_key_not_black():
    img = _sprite_with_black_outline()
    canvas, (bw, bh) = KleinPipeline._prep_input(img)
    key = key_color(img)
    assert canvas.getpixel((0, 0)) == key
    assert canvas.getpixel((canvas.width - 1, canvas.height - 1)) == key
    assert (bw, bh) == (512, 512)


def test_black_outline_survives_background_removal_on_a_key_bg():
    img = _sprite_with_black_outline()
    flat, _ = flatten_to_key(img)
    out = remove_background(flat.convert("RGBA"), tolerance=16)
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((4, 4)) == (0, 0, 0, 255)  # outline corner kept
    assert out.getpixel((6, 6))[3] == 255


def test_black_outline_is_eaten_on_a_black_bg():
    # regression guard: black padding matched the outline, so the flood crawled along it
    img = _sprite_with_black_outline()
    flat = Image.new("RGB", img.size, (0, 0, 0))
    flat.paste(img, (0, 0), img)
    out = remove_background(flat.convert("RGBA"), tolerance=16)
    assert out.getpixel((4, 4))[3] == 0
