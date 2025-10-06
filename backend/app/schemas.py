from __future__ import annotations

import base64
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field, field_validator


def _is_base64(s: str) -> bool:
    try:
        base64.b64decode(s.encode(), validate=True)
        return True
    except Exception:
        return False


# ---------- Keys ----------
class PublishKeyIn(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=64)
    pubkey_b64: str = Field(..., description="Chave pública (base64)")

    @field_validator("pubkey_b64")
    @classmethod
    def _validate_b64(cls, v: str) -> str:
        if not _is_base64(v):
            raise ValueError("pubkey_b64 não é base64 válido")
        return v


class KeyOut(BaseModel):
    client_id: str
    pubkey: str


# ---------- Clients ----------
class ClientsOut(BaseModel):
    clients: List[str]


# ---------- Messages (DM) ----------
class SendDMIn(BaseModel):
    to: str
    from_: str = Field(alias="from")
    blob: str
    meta: Optional[Dict[str, Any]] = None

    @field_validator("blob")
    @classmethod
    def _validate_blob_b64(cls, v: str) -> str:
        if not _is_base64(v):
            raise ValueError("Não é base64 válido")
        return v


class MessageOut(BaseModel):
    id: Optional[int] = None
    ts: Optional[float] = None
    from_: str = Field(alias="from")
    blob: str
    meta: Dict[str, Any] = {}
    type: Optional[str] = None  # como personalizar pra grupo?
    group_id: Optional[str] = None


class MessagesOut(BaseModel):
    messages: List[MessageOut]


# ---------- Groups ----------
class CreateGroupIn(BaseModel):
    group_id: str = Field(..., min_length=1, max_length=64)
    members: List[str]
    admin: str


class GroupsOut(BaseModel):
    groups: List[str]


class SendGroupIn(BaseModel):
    from_: str = Field(alias="from")
    blob: str

    @field_validator("blob")
    @classmethod
    def _validate_blob_b64(cls, v: str) -> str:
        if not _is_base64(v):
            raise ValueError("não é base64 válido")
        return v