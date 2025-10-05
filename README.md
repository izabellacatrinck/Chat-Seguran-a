# Chat Seguro

Chat seguro para a matéria de Segurança da Informação;
Inicialmente assegurando o pilar de Confidencialidade, por meio dos algoritmos de cifragem: Salsa20 e ECDH;

# MANUAL DE EXECUÇÃO

Gerar certificados: python server/generate_cert.py
Para subir o servidor: python server/server.py cert.pem key.pem
Para subir clientes: python client.py --id user --server localhost:4433 --cacert cert.pem
