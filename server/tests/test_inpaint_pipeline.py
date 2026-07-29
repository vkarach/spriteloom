import diffusers
from PIL import Image

from server.instruct import INPAINT_MARGIN, INPAINT_SUFFIX, STEPS, KleinPipeline


class _FakeResult:
    def __init__(self, images):
        self.images = images


class _FakeInpaintPipe:
    """Stand-in for Flux2KleinInpaintPipeline: records the call kwargs."""
    calls = []

    def __init__(self, **components):
        self.components = components

    def __call__(self, **kw):
        _FakeInpaintPipe.calls.append(kw)
        n = kw["num_images_per_prompt"]
        size = kw["image"].size
        mask = kw["mask_image"]
        img = Image.new("RGBA", size, (255, 0, 255, 255))
        img.paste(Image.new("RGBA", size, (10, 20, 30, 255)), (0, 0), mask)
        return _FakeResult([img.copy() for _ in range(n)])


class _FakeResidentPipe:
    """The five modules _inpaint_pipe_obj is expected to share, not reload."""
    vae = object()
    text_encoder = object()
    tokenizer = object()
    scheduler = object()
    transformer = object()


def _sprite():
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    for x in range(2, 6):
        for y in range(2, 6):
            img.putpixel((x, y), (140, 70, 40, 255))
    return img


def test_inpaint_shares_the_resident_modules_and_uses_a_full_schedule(monkeypatch):
    monkeypatch.setattr(diffusers, "Flux2KleinInpaintPipeline", _FakeInpaintPipe)
    _FakeInpaintPipe.calls.clear()

    pipe = KleinPipeline()
    pipe._pipe = _FakeResidentPipe()
    pipe._resolved = "bf16"
    monkeypatch.setattr(pipe, "load", lambda: None)

    img = _sprite()
    mask = Image.new("L", img.size, 0)
    for x in range(2, 6):
        for y in range(2, 4):
            mask.putpixel((x, y), 255)

    out = pipe.inpaint("add a hat", img, mask, variants=2, seeds=[1, 2])

    assert len(out) == 2
    assert len(_FakeInpaintPipe.calls) == 1  # bf16 chunk size covers both
    call = _FakeInpaintPipe.calls[0]
    assert call["strength"] == 1.0  # distilled model expects the full STEPS
    assert call["num_inference_steps"] == STEPS
    assert call["num_images_per_prompt"] == 2
    assert call["image"].size == call["mask_image"].size
    assert call["prompt"].endswith(INPAINT_SUFFIX)

    # same modules as the resident pipe: no second copy of the model in VRAM
    assert pipe._inpaint_pipe.components["vae"] is pipe._pipe.vae
    assert pipe._inpaint_pipe.components["transformer"] is pipe._pipe.transformer


# regression guard: the old paste-after-the-fact composite let the model's own fill leak in
def test_inpaint_mask_lines_up_with_the_prepped_image(monkeypatch):
    monkeypatch.setattr(diffusers, "Flux2KleinInpaintPipeline", _FakeInpaintPipe)
    _FakeInpaintPipe.calls.clear()

    pipe = KleinPipeline()
    pipe._pipe = _FakeResidentPipe()
    pipe._resolved = "bf16"
    monkeypatch.setattr(pipe, "load", lambda: None)

    img = _sprite()
    mask = Image.new("L", img.size, 0)
    mask.putpixel((3, 3), 255)  # a single selected pixel inside the subject

    pipe.inpaint("add a hat", img, mask, variants=1, seeds=[1])

    call = _FakeInpaintPipe.calls[0]
    big, big_mask = call["image"], call["mask_image"]
    assert big.size == big_mask.size
    sx, sy = big.width // img.width, big.height // img.height
    assert big_mask.getpixel((3 * sx, 3 * sy)) == 255
    assert big_mask.getpixel((0, 0)) == 0


def test_prep_mask_grows_by_the_margin(monkeypatch):
    mask = Image.new("L", (8, 8), 0)
    mask.putpixel((3, 3), 255)
    content_size = (32, 32)  # 4x upscale
    scale = content_size[0] // mask.width

    big_mask = KleinPipeline._prep_mask(mask, content_size, content_size)

    edge = INPAINT_MARGIN * scale
    cx, cy = 3 * scale, 3 * scale
    assert big_mask.getpixel((cx + edge, cy)) == 255  # just inside the margin
    assert big_mask.getpixel((cx + edge + scale, cy)) == 0  # past it


# regression guard: only the mask survives, so the inserted layer is just the new content
def test_inpaint_discards_everything_outside_the_mask(monkeypatch):
    monkeypatch.setattr(diffusers, "Flux2KleinInpaintPipeline", _FakeInpaintPipe)
    _FakeInpaintPipe.calls.clear()

    pipe = KleinPipeline()
    pipe._pipe = _FakeResidentPipe()
    pipe._resolved = "bf16"
    monkeypatch.setattr(pipe, "load", lambda: None)

    img = _sprite()
    mask = Image.new("L", img.size, 0)
    mask.putpixel((3, 3), 255)

    out = pipe.inpaint("add a hat", img, mask, variants=1, seeds=[1])[0]

    sx = out.width // img.width
    sy = out.height // img.height
    assert out.getpixel((0, 0))[3] == 0  # outside the mask: fully transparent
    assert out.getpixel((3 * sx, 3 * sy)) == (10, 20, 30, 255)  # the new content


# regression guard: a mask looser than the new content used to leave the model's own bg opaque
def test_inpaint_strips_background_left_inside_a_loose_mask(monkeypatch):
    pipe = KleinPipeline()
    pipe._pipe = _FakeResidentPipe()
    pipe._resolved = "bf16"
    monkeypatch.setattr(pipe, "load", lambda: None)

    img = _sprite()
    loose_mask = Image.new("L", img.size, 0)
    for x in range(1, 7):  # much wider than the single pixel the fake fills
        for y in range(1, 7):
            loose_mask.putpixel((x, y), 255)

    class _NarrowFakeInpaintPipe(_FakeInpaintPipe):
        def __call__(self, **kw):
            _FakeInpaintPipe.calls.append(kw)
            size = kw["image"].size
            content = Image.new("L", size, 0)
            content.putpixel((size[0] // 2, size[1] // 2), 255)
            img = Image.new("RGBA", size, (255, 0, 255, 255))
            img.paste(Image.new("RGBA", size, (10, 20, 30, 255)), (0, 0), content)
            return _FakeResult([img.copy()])

    monkeypatch.setattr(diffusers, "Flux2KleinInpaintPipeline",
                        _NarrowFakeInpaintPipe)

    out = pipe.inpaint("add a hat", img, loose_mask, variants=1, seeds=[1])[0]

    sx, sy = out.width // img.width, out.height // img.height
    corner_of_mask = (1 * sx, 1 * sy)
    assert out.getpixel(corner_of_mask)[3] == 0  # leftover bg stripped
