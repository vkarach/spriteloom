"""SpriteForge server: WebSocket endpoint on localhost."""
import asyncio
import json
import logging

import websockets

from server.protocol import ProtocolError, parse_request, error_msg

log = logging.getLogger("spriteforge")

# Replaced by the real generation handler in Task 9.
async def handle_request(ws, req):
    await ws.send(error_msg(req.id, "pipeline not loaded"))


async def _handler(ws):
    async for message in ws:
        try:
            data = json.loads(message)
            if isinstance(data, dict) and data.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue
        except json.JSONDecodeError:
            pass  # fall through to parse_request for a proper error
        try:
            req = parse_request(message)
        except ProtocolError as e:
            await ws.send(error_msg("", str(e)))
            continue
        try:
            await handle_request(ws, req)
        except Exception as e:  # never die silently
            log.exception("request failed")
            await ws.send(error_msg(req.id, f"{type(e).__name__}: {e}"))


async def serve(host="127.0.0.1", port=8765, stop=None, on_ready=None):
    async with websockets.serve(_handler, host, port, max_size=64 * 2**20):
        log.info("SpriteForge server on ws://%s:%s", host, port)
        if on_ready:
            on_ready()
        await (stop if stop is not None else asyncio.Future())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())
