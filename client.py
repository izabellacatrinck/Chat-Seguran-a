import argparse
import asyncio
import base64
import json
import os
import ssl
import time

from nacl.public import Box, PrivateKey, PublicKey
from nacl.secret import SecretBox


def b64(x: bytes) -> str:
    return base64.b64encode(x).decode()


def ub64(s: str) -> bytes:
    return base64.b64decode(s.encode())


class TLSSocketClient:
    def __init__(self, host, port, cafile=None):
        self.host = host
        self.port = port
        self.cafile = cafile

    async def send_recv(self, obj):
        try:
            sslctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            if self.cafile:
                sslctx.load_verify_locations(self.cafile)
            else:
                sslctx.check_hostname=False
                sslctx.verify_mode=ssl.CERT_NONE

            reader, writer = await asyncio.open_connection(self.host, self.port, ssl=sslctx)

            writer.write((json.dumps(obj)+"\n").encode())
            await writer.drain()

            line = await reader.readline()

            writer.close()
            await writer.wait_closed()

            if not line:
                return {"status": "error", "reason": "Nenhuma resposta recebida do servidor."}

            return json.loads(line.decode())
        except json.JSONDecodeError:
            return {"status": "error", "reason": "O servidor enviou uma resposta inválida."}
        except ConnectionRefusedError:
            return {"status": "error", "reason": "A conexão foi recusada. O servidor está offline?"}
        except Exception as e:
            return {"status": "error", "reason": f"Erro de conexão: {e}"}


async def interactive(server_host, server_port, cacert, client_id):
    client_id = client_id.strip().strip('"')
    client = TLSSocketClient(server_host, server_port, cacert)
    priv = PrivateKey.generate()
    pub = bytes(priv.public_key)

    # publica chave
    resp = await client.send_recv(
        {"type": "publish_key", "client_id": client_id, "pubkey": b64(pub)}
    )
    if resp.get("status") != "ok":
        print("Erro ao publicar chave:", resp)
        return
    print(f"[+] Chave pública publicada para {client_id}")

    conversations = {}  # peer_id -> [ (timestamp, sender, mensagem) ]
    groups = {}  # group_id -> { "key": bytes, "history": [] }
    new_msgs = {}  # peer_id -> int (novas mensagens)

    async def ainput(prompt=""):
        return await asyncio.to_thread(input, prompt)

    async def poll_blobs():
        while True:
            try:
                response = await client.send_recv(
                    {"type": "fetch_blobs", "client_id": client_id}
                )
                if response.get("status") == "ok":
                    for m in response.get("messages", []):
                        if m.get("type") == "group":
                            group_id = m["group_id"]
                            if group_id not in groups:
                                groups[group_id] = {
                                    "key": None,
                                    "history": [],
                                }  # Chave será recebida depois
                            groups[group_id]["history"].append(("received_group", m))
                            new_msgs[group_id] = new_msgs.get(group_id, 0) + 1
                        else:  # Mensagem privada
                            # Descriptografar a chave do grupo se for uma
                            try:
                                env = json.loads(base64.b64decode(m["blob"]).decode())
                                if env.get("type") == "group_key_distribution":
                                    peer_pub_b64 = env["sender_pub"]
                                    peer_pub = PublicKey(ub64(peer_pub_b64))
                                    box = Box(priv, peer_pub)
                                    group_key = box.decrypt(ub64(env["key_blob"]))
                                    group_id = env["group_id"]
                                    if group_id not in groups:
                                        groups[group_id] = {"history": []}
                                    groups[group_id]["key"] = group_key
                                    print(
                                        f"\n[GRUPO] Você foi adicionado ao grupo '{group_id}' e recebeu a chave."
                                    )
                                    continue  # Não armazena a chave como uma mensagem visível
                            except:  # noqa: S110
                                pass  # Não era uma chave de grupo

                            peer = m["from"]
                            if peer not in conversations:
                                conversations[peer] = []
                            conversations[peer].append(("received", m))
                            new_msgs[peer] = new_msgs.get(peer, 0) + 1
            except Exception as e:
                print(f"Erro no polling: {e}")
            await asyncio.sleep(1)

    poll_task = asyncio.create_task(poll_blobs())

    def show_menu():
        print("\nComandos disponíveis:")
        print(" - Listar (mostra usuários e grupos)")
        print(" - Iniciar chat <cliente>")
        print(" - Criar grupo <nome_grupo> com <membro1> <membro2> ...")
        print(" - Conversas (entra em chats privados ou de grupo)")
        print(" - Sair")

    show_menu()

    while True:
        line = await ainput(">> ")
        if not line:
            continue
        parts = line.strip().split(" ", 3)
        cmd = parts[0].lower()

        if cmd=="listar":
            try:
                resp = await client.send_recv({"type":"list_all","client_id":client_id})
                if resp.get("status")=="ok":
                    print("Clientes disponíveis:", resp.get("clients",[]))
                    print("Grupos disponíveis:", resp.get("groups",[]))
                else:
                    print(f"Erro ao listar: {resp.get('reason', 'causa desconhecida')}")
            except Exception as e:
                print("Erro:", e)

        elif cmd == "criar" and parts[1].lower() == "grupo":
            group_id = parts[2]
            members = parts[4:] + [client_id]  # Adiciona o criador à lista

            # 1. Gerar chave simétrica para o grupo
            group_key = os.urandom(SecretBox.KEY_SIZE)
            groups[group_id] = {"key": group_key, "history": []}

            # 2. Informar o servidor sobre o novo grupo
            await client.send_recv(
                {
                    "type": "create_group",
                    "group_id": group_id,
                    "members": members,
                    "admin": client_id,
                }
            )

            # 3. Distribuir a chave para cada membro
            print(f"[GRUPO] Distribuindo chave para o grupo '{group_id}'...")
            for member in members:
                if member == client_id:
                    continue

                # Pegar chave pública do membro
                resp = await client.send_recv({"type": "get_key", "client_id": member})
                if resp.get("status") != "ok":
                    print(f"  - Erro ao obter chave de {member}: {resp.get('reason')}")
                    continue

                peer_pub = PublicKey(ub64(resp["pubkey"]))
                box = Box(priv, peer_pub)

                # Cifrar a chave do grupo para este membro
                key_blob = box.encrypt(group_key)

                # Envelopar e enviar
                envelope = {
                    "type": "group_key_distribution",
                    "group_id": group_id,
                    "sender_pub": b64(pub),
                    "key_blob": b64(key_blob),
                }
                payload = {
                    "type": "send_blob",
                    "to": member,
                    "from": client_id,
                    "blob": b64(json.dumps(envelope).encode()),
                }
                await client.send_recv(payload)
                print(f"  - Chave enviada para {member}")

        elif cmd == "conversas":
            active_convs = list(conversations.keys()) + list(groups.keys())
            if not active_convs:
                print("Nenhuma conversa ativa.")
                continue

            print("Conversas ativas:")
            for peer in active_convs:
                count = new_msgs.get(peer, 0)
                msg_info = f" ({count} novas)" if count else ""
                conv_type = "[grupo]" if peer in groups else "[privado]"
                print(f" - {peer}{msg_info} {conv_type}")

            peer_choice = (
                await ainput("Entrar em qual conversa (ou Enter para voltar)? ")
            ).strip()
            if not peer_choice or peer_choice not in active_convs:
                continue

            peer = peer_choice
            new_msgs[peer] = 0

            # --- Lógica de Chat em Grupo ---
            if peer in groups:
                group = groups[peer]
                if not group.get("key"):
                    print("Aguardando recebimento da chave deste grupo.")
                    continue

                group_box = SecretBox(group["key"])
                print(f"=== Conversa em Grupo: {peer} === (digite /quit para sair)")

                # Mostrar histórico do grupo
                for entry in group["history"]:
                    ts = time.strftime("%H:%M:%S")
                    if entry[0] == "received_group":
                        m = entry[1]
                        try:
                            pt = group_box.decrypt(ub64(m["blob"]))
                            print(f"[{ts}] {m['from']}: {pt.decode()}")
                        except:
                            print(
                                f"[{ts}] {m['from']}: <erro ao decifrar mensagem de grupo>"
                            )
                    else:
                        ts, sender, msg = entry
                        print(f"[{ts}] {sender}: {msg}")

                # Loop de chat
                while True:
                    text = await ainput("")
                    if text.strip() == "/quit":
                        print(f"Saindo da conversa com {peer}.\n")
                        show_menu()
                        break

                    ts = time.strftime("%H:%M:%S")
                    cipher = group_box.encrypt(text.encode())
                    payload = {
                        "type": "send_group_blob",
                        "group_id": peer,
                        "from": client_id,
                        "blob": b64(cipher),
                    }
                    await client.send_recv(payload)
                    group["history"].append((ts, client_id, text))
                    print(f"[{ts}] {client_id}: {text}")
                continue  # Volta para o loop principal

            # --- Lógica de Chat Privado (existente) ---
            resp = await client.send_recv({"type": "get_key", "client_id": peer})
            if resp.get("status") != "ok":
                print("Não foi possível obter chave do peer:", resp)
                continue
            peer_pub = PublicKey(ub64(resp["pubkey"]))
            box = Box(priv, peer_pub)
            print(f"=== Conversa com {peer} === (digite /quit para sair)")

            for entry in conversations[peer]:
                ts = time.strftime("%H:%M:%S")
                if entry[0] == "received":
                    m = entry[1]
                    env = json.loads(base64.b64decode(m["blob"]).decode())
                    cipher = ub64(env["blob"])
                    sender_pub = PublicKey(ub64(env["sender_pub"]))
                    msg_box = Box(priv, sender_pub)
                    try:
                        pt = msg_box.decrypt(cipher)
                        print(f"[{ts}] {m['from']}: {pt.decode()}")
                    except:
                        print(f"[{ts}] {m['from']}: <erro ao decifrar>")
                else:
                    ts, sender, msg = entry
                    print(f"[{ts}] {sender}: {msg}")

            while True:
                text = await ainput("")
                if text.strip() == "/quit":
                    print(f"Saindo da conversa com {peer}.\n")
                    show_menu()
                    break
                ts = time.strftime("%H:%M:%S")
                cipher = box.encrypt(text.encode())
                envelope = {"sender_pub": b64(pub), "blob": b64(cipher)}
                payload = {
                    "type": "send_blob",
                    "to": peer,
                    "from": client_id,
                    "blob": b64(json.dumps(envelope).encode()),
                }
                await client.send_recv(payload)
                conversations[peer].append((ts, client_id, text))
                print(f"[{ts}] {client_id}: {text}")

        elif cmd == "iniciar":
            if len(parts) < 3 or parts[1].lower() != "chat":
                print("Uso: Iniciar chat com <cliente>")
                continue
            peer = parts[2].strip().strip('"')
            if peer == client_id:
                print("Não é possível iniciar chat consigo mesmo.")
                continue
            if peer not in conversations:
                conversations[peer] = []
            print(f"Conversa com {peer} criada. Use 'Conversas' para entrar nela.")

        elif cmd == "sair":
            print("Encerrando cliente...")
            poll_task.cancel()
            break
        else:
            print("Comando desconhecido.")
            show_menu()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--server", required=True)
    p.add_argument("--cacert")
    p.add_argument("--id", required=True)
    args = p.parse_args()
    host, port = args.server.split(":")
    asyncio.run(interactive(host, int(port), args.cacert, args.id))
