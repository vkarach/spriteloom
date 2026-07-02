import json
import pytest
from PIL import Image
from server.protocol import (
    ProtocolError, parse_request, progress_msg, result_msg, error_msg,
    image_to_b64, image_from_b64,
)


def _red_16():
    return Image.new("RGBA", (16, 16), (255, 0, 0, 255))


def test_image_b64_roundtrip():
    img = _red_16()
    out = image_from_b64(image_to_b64(img))
    assert out.size == (16, 16)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)


def test_parse_generate_request_defaults():
    req = parse_request(json.dumps({
        "id": "r1", "mode": "generate", "prompt": "demonic sword",
        "target_size": [64, 64], "frames": [],
    }))
    assert req.id == "r1"
    assert req.mode == "generate"
    assert req.variants == 4
    assert req.strength == 0.6
    assert req.target_size == (64, 64)
    assert req.frames == []


def test_parse_edit_request_decodes_frame():
    b64 = image_to_b64(_red_16())
    req = parse_request(json.dumps({
        "id": "r2", "mode": "edit", "prompt": "horse on two legs",
        "target_size": [16, 16], "strength": 0.4,
        "frames": [{"image": b64, "mask": None}],
    }))
    assert len(req.frames) == 1
    assert req.frames[0].image.size == (16, 16)
    assert req.frames[0].mask is None


def test_parse_rejects_bad_mode():
    with pytest.raises(ProtocolError):
        parse_request(json.dumps({
            "id": "x", "mode": "dream", "prompt": "p",
            "target_size": [16, 16], "frames": [],
        }))


def test_parse_rejects_edit_without_frame():
    with pytest.raises(ProtocolError):
        parse_request(json.dumps({
            "id": "x", "mode": "edit", "prompt": "p",
            "target_size": [16, 16], "frames": [],
        }))


def test_parse_rejects_invalid_json():
    with pytest.raises(ProtocolError):
        parse_request("{not json")


def test_parse_rejects_non_dict_frame():
    with pytest.raises(ProtocolError):
        parse_request(json.dumps({
            "id": "x", "mode": "generate", "prompt": "p",
            "target_size": [16, 16], "frames": [42],
        }))


def test_parse_rejects_inpaint_without_mask():
    b64 = image_to_b64(_red_16())
    with pytest.raises(ProtocolError):
        parse_request(json.dumps({
            "id": "x", "mode": "inpaint", "prompt": "p",
            "target_size": [16, 16],
            "frames": [{"image": b64, "mask": None}],
        }))


def test_response_builders():
    assert json.loads(progress_msg("r1", 0.5)) == {
        "id": "r1", "type": "progress", "value": 0.5}
    res = json.loads(result_msg("r1", [_red_16()]))
    assert res["type"] == "result" and len(res["images"]) == 1
    err = json.loads(error_msg("r1", "boom"))
    assert err == {"id": "r1", "type": "error", "message": "boom"}


def test_parse_instruct_requires_frame_image():
    with pytest.raises(ProtocolError):
        parse_request(json.dumps({
            "id": "x", "mode": "instruct", "prompt": "side view",
            "target_size": [16, 16], "frames": [],
        }))


def test_parse_instruct_accepts_frame():
    b64 = image_to_b64(_red_16())
    req = parse_request(json.dumps({
        "id": "i1", "mode": "instruct", "prompt": "side view",
        "target_size": [16, 16],
        "frames": [{"image": b64, "mask": None}],
    }))
    assert req.mode == "instruct"
    assert req.frames[0].image.size == (16, 16)


def test_progress_msg_stage_optional():
    assert "stage" not in json.loads(progress_msg("r", 0.5))
    msg = json.loads(progress_msg("r", 0.0, stage="Loading model..."))
    assert msg["stage"] == "Loading model..."
    assert msg["type"] == "progress"
