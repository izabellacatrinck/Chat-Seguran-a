import pathlib
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# gera chave privada RSA
key: rsa.RSAPrivateKey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
key_bytes: bytes = key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
)
pathlib.Path("key.pem").write_bytes(key_bytes)

# cria certificado autoassinado
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Amazonas"),
    x509.NameAttribute(NameOID.LOCALITY_NAME, "Manaus"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ChatSeguro"),
    x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.utcnow())
    .not_valid_after(datetime.utcnow() + timedelta(days=365))
    .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
    .sign(key, hashes.SHA256())
)

cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
pathlib.Path("cert.pem").write_bytes(cert_bytes)

print("✅ Arquivos gerados com sucesso:")
print(" - cert.pem (certificado público)")
print(" - key.pem  (chave privada do servidor)")
