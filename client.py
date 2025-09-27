import asyncio
import websockets
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

async def main():
    uri = "ws://localhost:8765"
    
    private_key = ec.generate_private_key(ec.SECP384R1())
    public_key = private_key.public_key()

    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    async with websockets.connect(uri) as websocket:
        print("Conectado ao servidor")

        await websocket.send(public_bytes.decode())
        print("Chave publica enviada ao servidor!")

        while True:
            msg = input("Digite uma mensagem: ")

            if msg.strip() == "!chave":
                print("Chave pública do cliente:\n")
                print(public_bytes.decode())
                continue

            if msg.strip() == "!sair":
                    print("Conexão encerrada.")
                    await websocket.close() 
                    break

            await websocket.send(msg)

if __name__ == "__main__":
    asyncio.run(main())
