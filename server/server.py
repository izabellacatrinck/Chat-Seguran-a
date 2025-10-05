#!/usr/bin/env python3
import asyncio, json, ssl, os
from argparse import ArgumentParser

PUBKEYS_FILE = "pubkeys.json"
PUBLIC_KEYS = {}    # client_id -> base64 pubkey
BLOBS = {}          # recipient_id -> [ {from, blob(base64), meta} ]
ACTIVE_CLIENTS = {} # client_id -> {reader, writer}

# --- Inicialização do JSON ---
def init_pubkeys():
    global PUBLIC_KEYS
    if os.path.exists(PUBKEYS_FILE):
        with open(PUBKEYS_FILE, "r") as f:
            try:
                PUBLIC_KEYS = json.load(f)
            except Exception as e:
                print("erro ao ler pubkeys.json, criando novo:", e)
                PUBLIC_KEYS = {}
    else:
        PUBLIC_KEYS = {}
        with open(PUBKEYS_FILE,"w") as f:
            json.dump(PUBLIC_KEYS, f, indent=2)
        print("✅ Arquivo pubkeys.json criado vazio.")

# --- Atualiza JSON ao receber nova chave ---
def store_pubkey(client_id, pubkey_b64):
    global PUBLIC_KEYS
    PUBLIC_KEYS[client_id] = pubkey_b64
    with open(PUBKEYS_FILE,"w") as f:
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
    addr = writer.get_extra_info('peername')
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
                try:
                    with open(PUBKEYS_FILE, "r") as f:
                        PUBLIC_KEYS = json.load(f)
                except FileNotFoundError:
                    PUBLIC_KEYS = {}
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
                BLOBS.setdefault(to, []).append({"from": frm, "blob": blob, "meta": meta})
                
                # --- NOVO: Log do transporte da mensagem cifrada ---
                print(f"[TRANSPORTE] Mensagem cifrada recebida de {frm} -> {to}: {blob}")

                await send_ok(writer, {"message": "stored"})

            elif mtype == "fetch_blobs":
                cid = msg.get("client_id")
                if not cid:
                    await send_error(writer, "fetch_blobs requer client_id")
                    continue
                items = BLOBS.pop(cid, [])
                await send_ok(writer, {"messages": items})

            elif mtype == "list_all":
                requester = msg.get("client_id")
                clients = [c for c in PUBLIC_KEYS.keys() if c != requester]
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
        try:
            await writer.wait_closed()
        except:
            pass

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
