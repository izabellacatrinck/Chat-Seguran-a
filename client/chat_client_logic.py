import asyncio
import base64
import json
import ssl
import time

from nacl.public import Box, PrivateKey, PublicKey


# Funções auxiliares (b64, ub64) permanecem as mesmas
def b64(x: bytes) -> str:
    return base64.b64encode(x).decode()

def ub64(s: str) -> bytes:
    return base64.b64decode(s.encode())

class TLSSocketClient:
    # A classe TLSSocketClient permanece a mesma
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
            if not line: return {"status": "error", "reason": "Nenhuma resposta recebida."}
            return json.loads(line.decode())
        except Exception as e:
            return {"status": "error", "reason": f"Erro de conexão: {e}"}

class ChatLogic:
    def __init__(self, server_host, server_port, cacert, client_id):
        self.client_id = client_id
        self.client = TLSSocketClient(server_host, server_port, cacert)
        self.priv = PrivateKey.generate()
        self.pub = bytes(self.priv.public_key)
        self.conversations = {}
        self.groups = {}
        self.on_new_message = None # Callback para a GUI

    async def publish_key(self):
        resp = await self.client.send_recv(
            {"type": "publish_key", "client_id": self.client_id, "pubkey": b64(self.pub)}
        )
        return resp.get("status") == "ok"

    async def list_all(self):
        resp = await self.client.send_recv({"type":"list_all","client_id":self.client_id})
        if resp.get("status")=="ok":
            return resp.get("clients",[]), resp.get("groups",[])
        return [], []

    async def send_private_message(self, peer, text):
        resp = await self.client.send_recv({"type": "get_key", "client_id": peer})
        if resp.get("status") != "ok":
            return False, f"Não foi possível obter a chave de {peer}"

        peer_pub = PublicKey(ub64(resp["pubkey"]))
        box = Box(self.priv, peer_pub)

        ts = time.strftime("%H:%M:%S")
        cipher = box.encrypt(text.encode())
        envelope = {"sender_pub": b64(self.pub), "blob": b64(cipher)}
        payload = {
            "type": "send_blob",
            "to": peer,
            "from": self.client_id,
            "blob": b64(json.dumps(envelope).encode()),
        }
        await self.client.send_recv(payload)
        if peer not in self.conversations: self.conversations[peer] = []
        self.conversations[peer].append((ts, self.client_id, text))
        return True, ""

    async def poll_blobs(self):
        """Este método agora chama um 'callback' para notificar a GUI."""
        while True:
            try:
                response = await self.client.send_recv(
                    {"type": "fetch_blobs", "client_id": self.client_id}
                )
                if response.get("status") == "ok":
                    for m in response.get("messages", []):
                        peer = m["from"]
                        # Lógica simplificada para demonstração
                        if self.on_new_message:
                            try:
                                # A lógica de decriptografia iria aqui
                                env = json.loads(base64.b64decode(m["blob"]).decode())
                                cipher = ub64(env["blob"])
                                sender_pub = PublicKey(ub64(env["sender_pub"]))
                                msg_box = Box(self.priv, sender_pub)
                                pt = msg_box.decrypt(cipher).decode()
                                # Chama o callback com a mensagem decifrada
                                self.on_new_message(peer, f"[{time.strftime('%H:%M:%S')}] {peer}: {pt}")
                            except Exception as e:
                                self.on_new_message(peer, f"[{time.strftime('%H:%M:%S')}] {peer}: <mensagem ilegível>")

            except Exception as e:
                print(f"Erro no polling: {e}")
            await asyncio.sleep(2) # Polling a cada 2 segundos