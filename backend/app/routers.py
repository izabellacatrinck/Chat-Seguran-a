from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import storage
from app.schemas import (
    PublishKeyIn, KeyOut,
    ClientsOut,
    SendDMIn, MessagesOut, MessageOut,
    CreateGroupIn, GroupsOut, SendGroupIn
)

# ---------- Health ----------
health_router = APIRouter(tags=["health"])

@health_router.get("/health")
async def health():
    return {"status": "ok"}

# ---------- Keys ----------
keys_router = APIRouter(prefix="/keys", tags=["keys"])

@keys_router.post("/publish")
async def publish_key(body: PublishKeyIn):
    storage.publish_key(body.client_id, body.pubkey_b64)
    return {"status": "ok"}

@keys_router.get("/{client_id}", response_model=KeyOut)
async def get_key(client_id: str):
    pk = storage.get_key(client_id)
    if not pk:
        raise HTTPException(404, "chave não encontrada")
    return KeyOut(client_id=client_id, pubkey=pk)

# ---------- Clients ----------
clients_router = APIRouter(prefix="/clients", tags=["clients"])

@clients_router.get("", response_model=ClientsOut)
async def list_clients(exclude: str | None = Query(default=None)):
    return ClientsOut(clients=storage.list_clients(exclude))

# ---------- Messages (DM) ----------
messages_router = APIRouter(prefix="/messages", tags=["messages"])

@messages_router.post("")
async def send_dm(body: SendDMIn):
    payload = {"from": body.from_, "blob": body.blob, "meta": body.meta or {}}
    await storage.enqueue_or_push(body.to, payload)
    return {"status": "ok"}

@messages_router.get("", response_model=MessagesOut)
async def fetch_blobs(
        client_id: str = Query(...),
        mode: str = Query("pending", pattern="^(pending|history)$"),
        peek: bool = Query(False),
        since_id: int | None = Query(default=None, ge=0),
        limit: int = Query(100, ge=1, le=1000),
):
    """
    mode=pending  -> retorna pendências (peek controla se consome ou não)
    mode=history  -> retorna histórico (ignora peek), suporta since_id/limit
    """
    if mode == "pending":
        items = storage.fetch_blobs(client_id, peek=peek)
    else:  # history
        items = storage.get_history(client_id, since_id=since_id, limit=limit)

    out = [MessageOut.model_validate({"from": m.get("from"), **m}) for m in items]
    return MessagesOut(messages=out)

# ---------- Groups ----------
groups_router = APIRouter(prefix="/groups", tags=["groups"])

@groups_router.post("")
async def create_group(body: CreateGroupIn):
    if storage.get_group(body.group_id):
        raise HTTPException(409, "grupo já existe")
    storage.create_group(body.group_id, body.members, body.admin)
    return {"status": "ok"}

@groups_router.get("", response_model=GroupsOut)
async def list_groups(member: str = Query(...)):
    return GroupsOut(groups=storage.list_groups_for_member(member))

@groups_router.post("/{group_id}/messages")
async def send_group_message(group_id: str, body: SendGroupIn):
    g = storage.get_group(group_id)
    if not g:
        raise HTTPException(404, "grupo não encontrado")
    if body.from_ not in g["members"]:
        raise HTTPException(403, "não é membro do grupo")
    await storage.fanout_group_message(group_id, body.from_, body.blob)
    return {"status": "ok"}