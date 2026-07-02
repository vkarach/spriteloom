"""JSON-over-WebSocket protocol: requests in, responses out, PNGs as base64."""
import base64
import io
import json
from dataclasses import dataclass, field

from PIL import Image

VALID_MODES = ("generate", "edit", "inpaint", "instruct")


class ProtocolError(Exception):
    pass


@dataclass
class Frame:
    image: Image.Image | None = None
    mask: Image.Image | None = None


@dataclass
class Request:
    id: str
    mode: str
    prompt: str
    target_size: tuple[int, int]
    variants: int = 4
    strength: float = 0.6
    frames: list[Frame] = field(default_factory=list)


def image_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def image_from_b64(s: str) -> Image.Image:
    try:
        return Image.open(io.BytesIO(base64.b64decode(s))).convert("RGBA")
    except Exception as e:
        raise ProtocolError(f"invalid image data: {e}") from e


def parse_request(text: str) -> Request:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid JSON: {e}") from e

    try:
        req_id = str(data["id"])
        mode = str(data["mode"])
        prompt = str(data["prompt"])
        w, h = data["target_size"]
        target_size = (int(w), int(h))
    except (KeyError, TypeError, ValueError) as e:
        raise ProtocolError(f"missing/invalid field: {e}") from e

    if mode not in VALID_MODES:
        raise ProtocolError(f"unknown mode '{mode}'")

    frames = []
    for f in data.get("frames", []):
        if not isinstance(f, dict):
            raise ProtocolError("frame entry must be an object")
        img = image_from_b64(f["image"]) if f.get("image") else None
        mask = image_from_b64(f["mask"]) if f.get("mask") else None
        frames.append(Frame(image=img, mask=mask))

    if mode in ("edit", "inpaint", "instruct") and (
            not frames or frames[0].image is None):
        raise ProtocolError(f"mode '{mode}' requires a frame image")
    if mode == "inpaint" and frames[0].mask is None:
        raise ProtocolError("inpaint requires a mask")

    return Request(
        id=req_id, mode=mode, prompt=prompt, target_size=target_size,
        variants=int(data.get("variants", 4)),
        strength=float(data.get("strength", 0.6)),
        frames=frames,
    )


def progress_msg(req_id: str, value: float, stage: str | None = None) -> str:
    data = {"id": req_id, "type": "progress", "value": value}
    if stage is not None:
        data["stage"] = stage
    return json.dumps(data)


def result_msg(req_id: str, images: list[Image.Image]) -> str:
    return json.dumps({
        "id": req_id, "type": "result",
        "images": [image_to_b64(i) for i in images],
    })


def error_msg(req_id: str, message: str) -> str:
    return json.dumps({"id": req_id, "type": "error", "message": message})
