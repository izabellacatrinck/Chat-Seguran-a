import asyncio
from http import server
import websockets

connected_clients = {}  

async def handler(websocket):
    print(f"Cliente conectado: {websocket.remote_address}")
    try:
        public_key_pem = await websocket.recv()
        connected_clients[websocket] = public_key_pem
        print(f"Chave publica recebida:\n{public_key_pem}")

        async for message in websocket:
            print(f"Mensagem recebida: {message}")

    except websockets.exceptions.ConnectionClosed:
        print(f"Cliente desconectado: {websocket.remote_address}")
    finally:
        connected_clients.pop(websocket, None)

async def main():
    async with websockets.serve(handler, "localhost", 8765):
        print("Servidor WebSocket rodando em ws://localhost:8765")
        try:
            await asyncio.Future()  # Mantém rodando
        except KeyboardInterrupt:
            print("\nServidor encerrado pelo usuário.")
            server.close()
            await server.wait_closed() 

if __name__ == "__main__":
    asyncio.run(main())
