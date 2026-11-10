"""Optional loopback HTTPS using project-private test certificates; never installs system trust."""

import argparse
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import psutil
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".tls-local"
STATE = PRIVATE / "state.json"
PORT = 8443


def create_certificates(directory):
    directory.mkdir(parents=True, exist_ok=False)
    if os.name == "nt":
        account = os.environ.get("USERDOMAIN", "") + "\\" + os.environ["USERNAME"]
        subprocess.run(
            ["icacls", str(directory), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)F"],
            capture_output=True,
            check=True,
        )
    else:
        directory.chmod(0o700)
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "SmartTicket Local Test CA " + uuid4().hex[:8])]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    for name, value in (("root.crt", ca), ("server.crt", leaf)):
        (directory / name).write_bytes(value.public_bytes(serialization.Encoding.PEM))
    for name, value in (("root.key", ca_key), ("server.key", key)):
        (directory / name).write_bytes(
            value.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        if os.name != "nt":
            (directory / name).chmod(0o600)
    return directory / "root.crt", directory / "server.crt", directory / "server.key"


def owned(process, certificate):
    try:
        args = process.cmdline()
        return Path(process.exe()).resolve() == (ROOT / ".conda/python.exe").resolve() and all(
            value in args
            for value in (
                "uvicorn",
                "backend.app.main:app",
                "--ssl-certfile",
                str(certificate),
                "--port",
                str(PORT),
            )
        )
    except psutil.Error:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    action = parser.parse_args().action
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    process = psutil.Process(state["pid"]) if state and psutil.pid_exists(state["pid"]) else None
    if action == "status":
        print(
            {
                "running": bool(process and owned(process, Path(state["certificate"]))),
                "url": f"https://127.0.0.1:{PORT}",
            }
        )
        return
    if process:
        if not owned(process, Path(state["certificate"])):
            raise RuntimeError("Refusing to replace or terminate an unrelated process")
        if action == "stop":
            process.terminate()
            process.wait(timeout=15)
            print("Project HTTPS listener stopped; certificates preserved, system trust unchanged.")
        return
    if action == "stop":
        print("Project HTTPS listener is already stopped.")
        return
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    if not PRIVATE.exists():
        PRIVATE.mkdir()
        if os.name == "nt":
            account = os.environ.get("USERDOMAIN", "") + "\\" + os.environ["USERNAME"]
            subprocess.run(
                ["icacls", str(PRIVATE), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)F"],
                capture_output=True,
                check=True,
            )
        else:
            PRIVATE.chmod(0o700)
    ca, certificate, key = create_certificates(
        PRIVATE / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    )
    with (PRIVATE / "server.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
                "--env-file",
                str(ROOT / ".env"),
                "--ssl-certfile",
                str(certificate),
                "--ssl-keyfile",
                str(key),
                "--no-access-log",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0,
        )
    STATE.write_text(
        json.dumps({"pid": process.pid, "ca": str(ca), "certificate": str(certificate)}),
        encoding="utf-8",
    )
    deadline = time.monotonic() + 30
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(ca)), trust_env=False, timeout=3
    ) as client:
        while time.monotonic() < deadline:
            try:
                if client.get(f"https://127.0.0.1:{PORT}/health/ready").status_code == 200:
                    print(
                        {
                            "url": f"https://127.0.0.1:{PORT}",
                            "certificate_verified_with_local_ca": True,
                            "system_trust_changed": False,
                            "expires_in_days": 30,
                        }
                    )
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise RuntimeError("HTTPS startup did not become ready; private log and certificates preserved")


if __name__ == "__main__":
    main()
