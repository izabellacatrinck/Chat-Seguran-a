#!/usr/bin/env python3 
import argparse, asyncio, ssl, base64, json, time
from nacl.public import PrivateKey, PublicKey, Box

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
        try: await writer.wait_closed()
        except: pass
        return json.loads(line.decode())

async def interactive(server_host, server_port, cacert, client_id):
    client_id = client_id.strip().strip('"')
    client = TLSSocketClient(server_host, server_port, cacert)
    priv = PrivateKey.generate()
    pub = bytes(priv.public_key)

    # publica chave
    resp = await client.send_recv({"type":"publish_key","client_id":client_id,"pubkey":b64(pub)})
    if resp.get("status")!="ok":
        print("Erro ao publicar chave:", resp)
        return
    print(f"[+] Chave pública publicada para {client_id}")

    conversations = {}  
    new_msgs = {}     

    async def ainput(prompt=""):
        return await asyncio.to_thread(input, prompt)

    async def poll_blobs():
        while True:
            try:
                blobs = await client.send_recv({"type":"fetch_blobs","client_id":client_id})
                if blobs.get("status")=="ok":
                    for m in blobs.get("messages",[]):
                        peer = m["from"]
                        if peer not in conversations:
                            conversations[peer] = []
                        conversations[peer].append(("received", m))
                        new_msgs[peer] = new_msgs.get(peer,0)+1
            except:
                pass
            await asyncio.sleep(1)

    poll_task = asyncio.create_task(poll_blobs())

    def show_menu():
        print("\nComandos disponíveis:")
        print(" - Listar clientes")
        print(" - Iniciar chat com <cliente>")
        print(" - Conversas")
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
                else:
                    print("Erro:", resp)
            except Exception as e:
                print("Erro:", e)

        elif cmd=="conversas":
            if not conversations:
                print("Nenhuma conversa ativa.")
                continue
            print("Conversas ativas:")
            for peer in conversations.keys():
                count = new_msgs.get(peer,0)
                msg_info = f" ({count} novas)" if count else ""
                print(f" - {peer}{msg_info}")
            peer_choice = await ainput("Entrar em qual conversa (ou Enter para voltar)? ")
            peer_choice = peer_choice.strip()
            if not peer_choice or peer_choice not in conversations:
                continue
            peer = peer_choice

            # pegar chave pública do peer
            resp = await client.send_recv({"type":"get_key","client_id":peer})
            if resp.get("status")!="ok":
                print("Não foi possível obter chave do peer:", resp)
                continue
            peer_pub = PublicKey(ub64(resp["pubkey"]))
            box = Box(priv, peer_pub)
            print(f"=== Conversa com {peer} === (digite /quit para sair)")

            # mostrar histórico
            history = conversations[peer]
            for entry in history:
                ts = time.strftime("%H:%M:%S")
                if entry[0]=="received":
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

            new_msgs[peer]=0

            stop_event = asyncio.Event()  

            async def chat_loop():
                while True:
                    text = await ainput("")
                    if text.strip() == "/quit":
                        print(f"Saindo da conversa com {peer}.\n")
                        stop_event.set()  # sinaliza refresh para parar
                        return "quit"
                    ts = time.strftime("%H:%M:%S")
                    cipher = box.encrypt(text.encode())
                    envelope = {"sender_pub": b64(pub), "blob": b64(cipher)}
                    payload = {"type":"send_blob","to":peer,"from":client_id,"blob":b64(json.dumps(envelope).encode())}
                    await client.send_recv(payload)
                    conversations[peer].append((ts, client_id, text))
                    print(f"[{ts}] {client_id}: {text}")

            async def refresh_loop():
                last_len = len(conversations[peer])
                while not stop_event.is_set():  # para quando stop_event for setado
                    await asyncio.sleep(1)
                    history = conversations[peer]
                    for idx in range(last_len, len(history)):
                        entry = history[idx]
                        if entry[0] == "received":
                            m = entry[1]
                            env = json.loads(base64.b64decode(m["blob"]).decode())
                            cipher = ub64(env["blob"])
                            sender_pub = PublicKey(ub64(env["sender_pub"]))
                            msg_box = Box(priv, sender_pub)
                            try:
                                pt = msg_box.decrypt(cipher)
                                ts = time.strftime("%H:%M:%S")
                                print(f"[{ts}] {m['from']}: {pt.decode()}")
                            except:
                                print(f"[{ts}] {m['from']}: <erro ao decifrar>")
                    last_len = len(history)

            chat_task = asyncio.create_task(chat_loop())
            refresh_task = asyncio.create_task(refresh_loop())

            result = await asyncio.gather(chat_task, refresh_task, return_exceptions=True)
            refresh_task.cancel()

            if any(r == "quit" for r in result if isinstance(r,str)):
                show_menu()
                continue  

        elif cmd=="iniciar":
            if len(parts)<3 or parts[1].lower()!="chat":
                print("Uso: Iniciar chat com <cliente>")
                continue
            peer = parts[2].strip().strip('"')
            if peer==client_id:
                print("Não é possível iniciar chat consigo mesmo.")
                continue
            if peer not in conversations:
                conversations[peer] = []
            print(f"Conversa com {peer} criada. Use 'Conversas' para entrar nela.")

        elif cmd=="sair":
            print("Encerrando cliente...")
            poll_task.cancel()
            break
        else:
            print("Comando desconhecido.")
            show_menu()

if __name__=="__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--server", required=True)
    p.add_argument("--cacert")
    p.add_argument("--id", required=True)
    args = p.parse_args()
    host, port = args.server.split(":")
    asyncio.run(interactive(host,int(port),args.cacert,args.id))
