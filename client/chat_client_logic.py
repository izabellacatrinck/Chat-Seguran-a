import asyncio
import base64
import json
import os
import ssl
import time
from pathlib import Path

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
                sslctx.check_hostname = False
                sslctx.verify_mode = ssl.CERT_NONE

            reader, writer = await asyncio.open_connection(
                self.host, self.port, ssl=sslctx
            )
            writer.write((json.dumps(obj) + "\n").encode())
            await writer.drain()
            line = await reader.readline()
            writer.close()
            await writer.wait_closed()
            if not line:
                return {"status": "error", "reason": "Nenhuma resposta recebida."}
            return json.loads(line.decode())
        except Exception as e:
            return {"status": "error", "reason": f"Erro de conexão: {e}"}


class ChatLogic:
    def __init__(self, server_host, server_port, cacert, client_id):
        self.client_id = client_id
        self.client = TLSSocketClient(server_host, server_port, cacert)
        self.priv, self.pub = self.load_or_create_keys(client_id)

        self.conversations = {}
        self.on_new_message = None
        self.on_update_ui = None

    def load_or_create_keys(self, client_id):
        key_file = Path(f"{client_id}_key.pem")
        if key_file.exists():
            print(f"Bem-vindo de volta, {client_id}! Carregando sua chave.")
            priv_bytes = ub64(key_file.read_text())
            priv = PrivateKey(priv_bytes)
        else:
            print(f"Primeiro login de {client_id}. Gerando novo par de chaves.")
            priv = PrivateKey.generate()
            key_file.write_text(b64(bytes(priv)))

        pub = bytes(priv.public_key)
        return priv, pub

    async def publish_key(self):
        resp = await self.client.send_recv(
            {
                "type": "publish_key",
                "client_id": self.client_id,
                "pubkey": b64(self.pub),
            }
        )
        return resp.get("status") == "ok"

    async def list_all(self):
        resp = await self.client.send_recv(
            {"type": "list_all", "client_id": self.client_id}
        )
        if resp.get("status") == "ok":
            clients = resp.get("clients", [])
            groups = resp.get("groups", [])

            for client in clients:
                if client not in self.conversations:
                    self.conversations[client] = {"history": [], "type": "private"}
            for group in groups:
                if group not in self.conversations:
                    self.conversations[group] = {
                        "key": None,
                        "history": [],
                        "type": "group",
                    }
            return clients, groups
        return [], []

    async def create_group(self, group_id, members):
        if self.client_id not in members:
            members.append(self.client_id)
        group_key = os.urandom(SecretBox.KEY_SIZE)
        self.conversations[group_id] = {
            "key": group_key,
            "history": [],
            "type": "group",
        }

        await self.client.send_recv(
            {
                "type": "create_group",
                "group_id": group_id,
                "members": members,
                "admin": self.client_id,
            }
        )
        print(f"[GRUPO] Grupo '{group_id}' criado. Distribuindo chave...")

        for member in members:
            if member == self.client_id:
                continue
            resp = await self.client.send_recv({"type": "get_key", "client_id": member})
            if resp.get("status") != "ok":
                print(f"Erro ao obter chave de {member}: {resp.get('reason')}")
                continue

            peer_pub = PublicKey(ub64(resp["pubkey"]))
            box = Box(self.priv, peer_pub)
            key_blob = box.encrypt(group_key)

            envelope = {
                "type": "group_key_distribution",
                "group_id": group_id,
                "sender_pub": b64(self.pub),
                "key_blob": b64(key_blob),
            }
            payload = {
                "type": "send_blob",
                "to": member,
                "from": self.client_id,
                "blob": b64(json.dumps(envelope).encode()),
            }
            await self.client.send_recv(payload)
            print(f"  - Chave enviada para {member}")

        ts = time.strftime("%H:%M:%S")
        self.conversations[group_id]["history"].append(
            (ts, "Sistema", f"Você criou o grupo '{group_id}'.")
        )
        if self.on_update_ui:
            self.on_update_ui()

    async def send_private_message(self, peer, text):
        resp = await self.client.send_recv({"type": "get_key", "client_id": peer})
        if resp.get("status") != "ok":
            return False, f"Não foi possível obter a chave de {peer}"
        peer_pub = PublicKey(ub64(resp["pubkey"]))
        box = Box(self.priv, peer_pub)

        cipher = box.encrypt(text.encode())
        envelope = {"sender_pub": b64(self.pub), "blob": b64(cipher)}
        payload = {
            "type": "send_blob",
            "to": peer,
            "from": self.client_id,
            "blob": b64(json.dumps(envelope).encode()),
        }
        await self.client.send_recv(payload)
        return True, ""

    async def send_group_message(self, group_id, text):
        if (
            group_id not in self.conversations
            or self.conversations[group_id]["type"] != "group"
        ):
            return False, "Grupo não encontrado."

        group_data = self.conversations[group_id]
        if not group_data.get("key"):
            return False, "A chave deste grupo ainda não foi recebida."

        group_box = SecretBox(group_data["key"])
        cipher = group_box.encrypt(text.encode())

        payload = {
            "type": "send_group_blob",
            "group_id": group_id,
            "from": self.client_id,
            "blob": b64(cipher),
        }
        await self.client.send_recv(payload)
        return True, ""

    async def poll_blobs(self):
        while True:
            await asyncio.sleep(2)
            try:
                response = await self.client.send_recv(
                    {"type": "fetch_blobs", "client_id": self.client_id}
                )
                if response.get("status") == "ok":
                    for m in response.get("messages", []):
                        ts = time.strftime("%H:%M:%S")

                        # **LÓGICA CORRIGIDA AQUI**
                        # Verifica primeiro se é uma mensagem de grupo, pois o seu blob não é JSON.
                        if m.get("type") == "group":
                            group_id = m["group_id"]
                            if group_id in self.conversations and self.conversations[
                                group_id
                            ].get("key"):
                                group_box = SecretBox(
                                    self.conversations[group_id]["key"]
                                )
                                try:
                                    pt = group_box.decrypt(ub64(m["blob"])).decode()
                                    self.conversations[group_id]["history"].append(
                                        (ts, m["from"], pt)
                                    )
                                    if self.on_new_message:
                                        self.on_new_message(
                                            group_id, f"[{ts}] {m['from']}: {pt}"
                                        )
                                except Exception as e:
                                    print(f"Erro ao descriptografar msg de grupo: {e}")
                            continue  # Processou a mensagem de grupo, vai para a próxima.

                        # Se não for uma mensagem de grupo, pode ser uma mensagem privada ou uma chave.
                        try:
                            # Tenta decodificar o blob como um envelope JSON
                            env = json.loads(base64.b64decode(m["blob"]).decode())

                            # Verifica se é uma distribuição de chave de grupo
                            if env.get("type") == "group_key_distribution":
                                peer_pub = PublicKey(ub64(env["sender_pub"]))
                                box = Box(self.priv, peer_pub)
                                group_key = box.decrypt(ub64(env["key_blob"]))
                                group_id = env["group_id"]

                                if group_id not in self.conversations:
                                    self.conversations[group_id] = {
                                        "history": [],
                                        "type": "group",
                                    }
                                self.conversations[group_id]["key"] = group_key

                                welcome_msg = (
                                    f"Você foi adicionado ao grupo '{group_id}'."
                                )
                                self.conversations[group_id]["history"].append(
                                    (ts, "Sistema", welcome_msg)
                                )
                                if self.on_new_message:
                                    self.on_new_message(
                                        group_id, f"[{ts}] Sistema: {welcome_msg}"
                                    )
                                if self.on_update_ui:
                                    self.on_update_ui()

                            # Senão, assume que é uma mensagem privada normal
                            else:
                                peer = m["from"]
                                if peer not in self.conversations:
                                    self.conversations[peer] = {
                                        "history": [],
                                        "type": "private",
                                    }

                                cipher = ub64(env["blob"])
                                sender_pub = PublicKey(ub64(env["sender_pub"]))
                                msg_box = Box(self.priv, sender_pub)
                                pt = msg_box.decrypt(cipher).decode()

                                self.conversations[peer]["history"].append(
                                    (ts, peer, pt)
                                )
                                if self.on_new_message:
                                    self.on_new_message(peer, f"[{ts}] {peer}: {pt}")

                        except Exception as e:
                            print(
                                f"Erro ao processar blob JSON de {m.get('from')}: {e}"
                            )

            except Exception as e:
                print(f"Erro no polling: {e}")
