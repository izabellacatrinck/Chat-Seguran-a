from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from fastapi import WebSocket

# --- Estruturas em memória ---
PUBKEYS: Dict[str, str] = {}                      # client_id -> base64 pubkey

# Pendências (fila para polling)
DM_QUEUES: Dict[str, List[dict]] = {}             # recipient -> [ msg ]

# Histórico completo por destinatário (não é consumido)
DM_HISTORY: Dict[str, List[dict]] = {}            # recipient -> [ msg ]

# Sequência por destinatário
DM_SEQ: Dict[str, int] = {}                       # recipient -> last_id

GROUPS: Dict[str, dict] = {}                      # group_id -> {"members":[...], "admin": str}

WS_CLIENTS: Dict[str, "WSConn"] = {}              # client_id -> WSConn

@dataclass
class WSConn:
    ws: WebSocket
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)

# ---------- Keys/Clients ----------
def publish_key(client_id: str, pubkey_b64: str) -> None:
    PUBKEYS[client_id] = pubkey_b64

def get_key(client_id: str) -> Optional[str]:
    return PUBKEYS.get(client_id)

def list_clients(exclude: Optional[str] = None) -> List[str]:
    return [c for c in PUBKEYS if c != (exclude or "")]

# ---------- WebSocket registry ----------
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
    while True:
        payload = await conn.queue.get()
        await conn.ws.send_json({"type": "message", **payload})

# ---------- Helpers internos ----------
def _next_id(recipient: str) -> int:
    DM_SEQ[recipient] = DM_SEQ.get(recipient, 0) + 1
    return DM_SEQ[recipient]

def _record(recipient: str, payload: dict) -> dict:
    """Anexa id/ts e grava em histórico."""
    msg = {"id": _next_id(recipient), "ts": time.time(), **payload}
    DM_HISTORY.setdefault(recipient, []).append(msg)
    return msg

# ---------- DM (enqueue/push) ----------
async def enqueue_or_push(recipient: str, payload: dict) -> None:
    """
    Enfileira (pendente) + grava em histórico.
    Se o usuário tiver WS conectado, faz push em tempo real (sem duplicar na fila).
    """
    msg = _record(recipient, payload)

    conn = WS_CLIENTS.get(recipient)
    if conn:
        await conn.queue.put(msg)
    else:
        DM_QUEUES.setdefault(recipient, []).append(msg)

# ---------- Fetch pendências / histórico ----------
def fetch_blobs(client_id: str, *, peek: bool = False) -> List[dict]:
    """
    Retorna as pendências do cliente.
    - peek=True -> NÃO consome (copia).
    - peek=False (default) -> consome (pop).
    """
    if peek:
        return list(DM_QUEUES.get(client_id, []))
    return DM_QUEUES.pop(client_id, [])

def get_history(client_id: str, *, since_id: Optional[int] = None, limit: int = 100) -> List[dict]:
    """
    Retorna histórico (não consome). Usa cursor por id.
    """
    hist = DM_HISTORY.get(client_id, [])
    if since_id is not None:
        hist = [m for m in hist if (m.get("id") or 0) > since_id]
    if limit is not None and limit > 0:
        hist = hist[:limit]
    return hist

# ---------- Grupos ----------
def create_group(group_id: str, members: List[str], admin: str) -> None:
    unique_members = list(dict.fromkeys(members + [admin]))
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
        # cada destinatário recebe um registro com id/ts próprios
        tasks.append(enqueue_or_push(m, {"from": sender, "group_id": group_id, "type": "group", "blob": blob_b64}))
    if tasks:
        await asyncio.gather(*tasks)
