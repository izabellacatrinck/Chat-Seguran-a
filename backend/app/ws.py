from __future__ import annotations

import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.storage import ws_register, ws_unregister, ws_send_from_queue

ws_router = APIRouter(tags=["websocket"])

@ws_router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        hello = await ws.receive_json()
        if hello.get("type") != "hello" or "client_id" not in hello:
            await ws.close(code=1002)
            return

        client_id = hello["client_id"]
        conn = await ws_register(client_id, ws)

        sender = asyncio.create_task(ws_send_from_queue(conn))
        receiver = asyncio.create_task(_ws_receiver(ws))

        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)

    except WebSocketDisconnect:
        pass
    finally:
        ws_unregister(ws)


async def _ws_receiver(ws: WebSocket):
    """Recebe mensagens do cliente (keep-alives, acks etc.)."""
    while True:
        await ws.receive_text()
