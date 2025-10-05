#!/usr/bin/env python3
import asyncio
import builtins
import contextlib
import json
import ssl
from argparse import ArgumentParser
from pathlib import Path

PUBKEYS_FILE = Path("pubkeys.json")
PUBLIC_KEYS = {}  # client_id -> base64 pubkey
BLOBS = {}  # recipient_id -> [ {from, blob(base64), meta} ]
ACTIVE_CLIENTS = {}  # client_id -> {reader, writer}
GROUPS = {}  # group_id -> { "members": [client_id], "admin": client_id }


# --- Inicialização do JSON ---
def init_pubkeys():
    global PUBLIC_KEYS
    if PUBKEYS_FILE.exists():
        with PUBKEYS_FILE.open("r") as f:
            try:
                PUBLIC_KEYS = json.load(f)
            except Exception as e:
                print("erro ao ler pubkeys.json, criando novo:", e)
                PUBLIC_KEYS = {}
    else:
        PUBLIC_KEYS = {}
        with PUBKEYS_FILE.open("w") as f:
            json.dump(PUBLIC_KEYS, f, indent=2)
        print("✅ Arquivo pubkeys.json criado vazio.")


# --- Atualiza JSON ao receber nova chave ---
def store_pubkey(client_id, pubkey_b64):
    global PUBLIC_KEYS
    PUBLIC_KEYS[client_id] = pubkey_b64
    with PUBKEYS_FILE.open("w") as f:
        json.dump(PUBLIC_KEYS, f, indent=2)
    print(f"[+] Nova chave pública recebida de {client_id}: {pubkey_b64}")


# --- Funções auxiliares ---
async def send_ok(writer, payload):
    obj = {"status": "ok", **payload}
    writer.write((json.dumps(obj) + "\n").encode())
    await writer.drain()


async def send_error(writer, reason):
    obj = {"status": "error", "reason": reason}
    writer.write((json.dumps(obj) + "\n").encode())
    await writer.drain()


# --- Handler de conexões ---
async def handle_reader(reader, writer):
    addr = writer.get_extra_info("peername")
    client_id = None
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode())
            except Exception as e:
                await send_error(writer, f"invalid json: {e}")
                continue

            mtype = msg.get("type")

            if mtype == "publish_key":
                cid = msg.get("client_id")
                pub = msg.get("pubkey")
                if not cid or not pub:
                    await send_error(writer, "publish_key requer client_id e pubkey")
                    continue

                store_pubkey(cid, pub)

                if cid not in ACTIVE_CLIENTS:
                    print(f"[+] Novo cliente registrado: {cid} ({addr})")
                    ACTIVE_CLIENTS[cid] = {"reader": reader, "writer": writer}

                client_id = cid
                await send_ok(writer, {"message": "key stored", "client_id": cid})

            elif mtype == "get_key":
                cid = msg.get("client_id")
                if not cid:
                    await send_error(writer, "get_key requer client_id")
                    continue

                pub = PUBLIC_KEYS.get(cid)

                if not pub:
                    await send_error(writer, "não encontrado")
                else:
                    print(f"[INFO] Enviando chave pública de {cid}")
                    await send_ok(writer, {"client_id": cid, "pubkey": pub})

            elif mtype == "send_blob":
                to = msg.get("to")
                frm = msg.get("from")
                blob = msg.get("blob")
                meta = msg.get("meta", {})
                if not to or not frm or not blob:
                    await send_error(writer, "send_blob requer to, from e blob")
                    continue
                BLOBS.setdefault(to, []).append(
                    {"from": frm, "blob": blob, "meta": meta}
                )

                # log do transporte da mensagem cifrada
                print(
                    f"[TRANSPORTE] Mensagem cifrada recebida de {frm} -> {to}: {blob}"
                )
                await send_ok(writer, {"message": "stored"})

            elif mtype == "create_group":
                group_id = msg.get("group_id")
                members = msg.get("members")
                admin = msg.get("admin")
                if not group_id or not members or not admin:
                    await send_error(
                        writer, "create_group requer group_id, members e admin"
                    )
                    continue
                if group_id in GROUPS:
                    await send_error(writer, "grupo já existe")
                    continue

                GROUPS[group_id] = {"members": members, "admin": admin}
                print(
                    f"[GRUPO] Novo grupo criado: {group_id} por {admin} com membros {members}"
                )
                await send_ok(writer, {"message": "group created"})

            elif mtype == "send_group_blob":
                group_id = msg.get("group_id")
                frm = msg.get("from")
                blob = msg.get("blob")
                if not group_id or not frm or not blob:
                    await send_error(
                        writer, "send_group_blob requer group_id, from e blob"
                    )
                    continue
                if group_id not in GROUPS:
                    await send_error(writer, "grupo não encontrado")
                    continue

                group = GROUPS[group_id]
                if frm not in group["members"]:
                    await send_error(writer, "você não é membro deste grupo")
                    continue

                print(
                    f"[GRUPO TRANSPORTE] Mensagem recebida de {frm} para o grupo {group_id}"
                )

            elif mtype == "fetch_blobs":
                cid = msg.get("client_id")
                if not cid:
                    await send_error(writer, "fetch_blobs requer client_id")
                    continue
                items = BLOBS.pop(cid, [])
                await send_ok(writer, {"messages": items})

            elif mtype == "list_all":
                requester = msg.get("client_id")
                clients = [c for c in PUBLIC_KEYS if c != requester]
                await send_ok(writer, {"clients": clients})

            # --- NOVO: desconexão explícita ---
            elif mtype == "disconnect":
                cid = msg.get("client_id")
                if cid and cid in ACTIVE_CLIENTS:
                    del ACTIVE_CLIENTS[cid]
                    print(f"[+] Cliente desconectado: {cid}")
                await send_ok(writer, {"message": "disconnected"})
                break

            else:
                await send_error(writer, "unknown_type")

    except Exception as e:
        print(f"[ERRO] Conexão com {client_id or addr} caiu: {e}")
    finally:
        writer.close()
        with contextlib.suppress(builtins.BaseException):
            await writer.wait_closed()


# --- Main ---
async def main(certfile, keyfile, host="0.0.0.0", port=4433):
    sslctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    sslctx.load_cert_chain(certfile, keyfile)
    server = await asyncio.start_server(handle_reader, host, port, ssl=sslctx)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    print(f"Servidor rodando em {addrs}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    init_pubkeys()
    p = ArgumentParser()
    p.add_argument("certfile")
    p.add_argument("keyfile")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", default=4433, type=int)
    args = p.parse_args()
    try:
        asyncio.run(main(args.certfile, args.keyfile, args.host, args.port))
    except KeyboardInterrupt:
        print("Shutting down...")
