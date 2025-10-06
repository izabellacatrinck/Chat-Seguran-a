from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from fastapi import WebSocket

PUBKEYS: Dict[str, str] = {}                      # client_id -> base64 pubkey
DM_QUEUES: Dict[str, List[dict]] = {}             # recipient -> [ {from, blob, meta, ...} ]
GROUPS: Dict[str, dict] = {}                      # group_id -> {"members":[...], "admin": str}
WS_CLIENTS: Dict[str, "WSConn"] = {}              # client_id -> WSConn


@dataclass
class WSConn:
    ws: WebSocket
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


# =========================
# Funções de chaves/usuários
# =========================
def publish_key(client_id: str, pubkey_b64: str) -> None:
    PUBKEYS[client_id] = pubkey_b64


def get_key(client_id: str) -> Optional[str]:
    return PUBKEYS.get(client_id)


def list_clients(exclude: Optional[str] = None) -> List[str]:
    return [c for c in PUBKEYS if c != (exclude or "")]


# =========================
# WebSocket registry
# =========================
async def ws_register(client_id: str, ws: WebSocket) -> WSConn:
    conn = WSConn(ws=ws)
    WS_CLIENTS[client_id] = conn

    for m in DM_QUEUES.pop(client_id, []):
        await conn.queue.put(m)
    return conn


def ws_unregister(ws: WebSocket) -> None:
    for cid, c in list(WS_CLIENTS.items()):
        if c.ws is ws:
            WS_CLIENTS.pop(cid, None)
            break


async def ws_send_from_queue(conn: WSConn) -> None:
    """Toma mensagens da fila e envia no WS."""
    while True:
        payload = await conn.queue.get()
        await conn.ws.send_json({"type": "message", **payload})


# =========================
# Mensagens privadas (DM)
# =========================
async def enqueue_or_push(recipient: str, payload: dict) -> None:
    """Se o usuário tem WS aberto, empurra em tempo real; caso contrário, enfileira."""
    conn = WS_CLIENTS.get(recipient)
    if conn:
        await conn.queue.put(payload)
    else:
        DM_QUEUES.setdefault(recipient, []).append(payload)


def fetch_blobs(client_id: str) -> List[dict]:
    """Retorna e limpa a fila de mensagens do cliente."""
    return DM_QUEUES.pop(client_id, [])


# =========================
# Grupos
# =========================
def create_group(group_id: str, members: List[str], admin: str) -> None:
    unique_members = list(dict.fromkeys(members + [admin]))     # normaliza membros (sem duplicatas) e garante admin incluso
    GROUPS[group_id] = {"members": unique_members, "admin": admin}


def get_group(group_id: str) -> Optional[dict]:
    return GROUPS.get(group_id)


def list_groups_for_member(member: str) -> List[str]:
    return [gid for gid, g in GROUPS.items() if member in g["members"]]


async def fanout_group_message(group_id: str, sender: str, blob_b64: str) -> None:
    g = get_group(group_id)
    if not g:
        return
    tasks = []
    for m in g["members"]:
        if m == sender:
            continue
        tasks.append(
            enqueue_or_push(
                m,
                {"from": sender, "group_id": group_id, "type": "group", "blob": blob_b64},
            )
        )
    if tasks:
        await asyncio.gather(*tasks)
