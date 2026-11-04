"""
Levanta la API y la página de prueba para calificar desde el celular.

Uso:
    python -m api.serve              # https://<ip-de-la-compu>:8000
    python -m api.serve -v           # viendo el flujo
    python -m api.serve --http       # sin HTTPS (solo sirve "subir foto" en el celular)

El celular y la computadora deben estar en la misma red Wi-Fi. Al arrancar
se imprime la dirección y un QR en la terminal para abrirla desde el
celular.

El navegador solo deja usar la cámara en vivo en páginas HTTPS, así que se
genera un certificado propio (en data/certs/). El celular avisará que "la
conexión no es privada": es normal con un certificado propio; toca
"Configuración avanzada" → "Continuar". En la web desplegada (Render,
Vercel) el HTTPS ya es real y no sale ese aviso.
"""

import argparse
import ipaddress
import logging
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import uvicorn
from sqlalchemy.orm import sessionmaker

from api.auth import Auth
from api.main import WEB_DIST, create_app
from cli.logs import add_verbose_flag, setup_logging
from storage.db import init_db, make_engine

CERT_DIR = Path(__file__).resolve().parent.parent / "data" / "certs"

logger = logging.getLogger(__name__)


def lan_ip() -> str:
    """IP de la computadora en la red local (la que usa para salir a internet)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))  # no manda nada; solo elige la interfaz
            ip = s.getsockname()[0]
        except OSError:
            ip = "127.0.0.1"
    logger.info("IP local: %s", ip)
    return ip


def ensure_certificate(ip: str) -> tuple[Path, Path]:
    """Certificado propio para HTTPS. Se regenera si no existe o si la IP
    de la computadora cambió."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path, key_path = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ipaddress.ip_address(ip) in san.get_values_for_type(x509.IPAddress):
            logger.info("certificado existente en %s", cert_path)
            return cert_path, key_path
        logger.info("la IP cambió; se genera otro certificado")

    logger.info("generando certificado propio para %s", ip)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Calificador (local)")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.IPAddress(ipaddress.ip_address(ip)),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    logger.info("certificado guardado en %s", cert_path)
    return cert_path, key_path


def print_qr(text: str) -> None:
    """Dibuja un QR en la terminal (dos módulos por carácter)."""
    qr = cv2.QRCodeEncoder.create().encode(text)  # 0 = negro, 255 = blanco, con margen
    dark = qr < 128
    if dark.shape[0] % 2:
        dark = cv2.copyMakeBorder(dark.astype("uint8"), 0, 1, 0, 0,
                                  cv2.BORDER_CONSTANT, value=0).astype(bool)
    chars = {(False, False): "█", (True, False): "▄", (False, True): "▀", (True, True): " "}
    for top, bottom in zip(dark[0::2], dark[1::2]):
        print("".join(chars[(bool(t), bool(b))] for t, b in zip(top, bottom)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="0.0.0.0", help="Interfaz (0.0.0.0 = toda la red)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--http", action="store_true",
                        help="Sin HTTPS: la cámara en vivo solo funciona en la misma compu")
    add_verbose_flag(parser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    if args.verbose == 1:
        # Igual que cli.scan: sin el detalle de core/ en cada cuadro
        logging.getLogger("core").setLevel(logging.ERROR)
        logging.getLogger("core.live").setLevel(logging.INFO)
        logging.getLogger("core.pipeline").setLevel(logging.INFO)
    logger.info("inicio: host=%s port=%d http=%s", args.host, args.port, args.http)

    engine = make_engine()
    init_db(engine)
    app = create_app(sessionmaker(engine), Auth.from_env())

    ip = lan_ip()
    ssl = {}
    if not args.http:
        cert, key = ensure_certificate(ip)
        ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    url = f"{'http' if args.http else 'https'}://{ip}:{args.port}"

    print(f"\nAbre en el celular (misma red Wi-Fi):  {url}\n")
    print_qr(url)
    if not args.http:
        print("\nSi sale \"la conexión no es privada\": Configuración avanzada → Continuar.")
    if not (WEB_DIST / "index.html").exists():
        print("\nAVISO: la web no está compilada; en / sale la página de prueba.")
        print("       Compílala con: cd web && npm install && npm run build")
    print("Ctrl+C para detener.\n")

    # log_config=None: uvicorn usa la misma configuración de logs que el resto
    uvicorn.run(app, host=args.host, port=args.port, log_config=None, **ssl)


if __name__ == "__main__":
    main()
