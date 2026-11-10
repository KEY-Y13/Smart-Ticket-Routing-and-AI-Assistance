"""Prepare a TLS-verified Linux wheelhouse on Windows, without installing foreign wheels."""

import hashlib
import json
import os
import runpy
import socket
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
retrieval = "--retrieval" in sys.argv
use_proxy = "--proxy" in sys.argv
mapping = json.loads((root / "artifacts/docker-build-dns-provenance.json").read_text())["addresses"]
original = socket.getaddrinfo


def resolved(host, *args, **kwargs):
    # Change only this download process's resolution; URL/SNI/certificate hostname stay original.
    return original(mapping.get(host, host), *args, **kwargs)


socket.getaddrinfo = resolved
if not use_proxy:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(name, None)
target = root / ("artifacts/wheels-retrieval-linux" if retrieval else "artifacts/wheels-linux")
target.mkdir(exist_ok=True)
requirements = "requirements-retrieval.txt" if retrieval else "requirements-runtime.txt"
sys.argv = [
    "pip",
    "download",
    "--only-binary=:all:",
    "--python-version=312",
    "--implementation=cp",
    "--abi=cp312",
    "--abi=abi3",
    "--abi=none",
    "--dest",
    str(target),
    "--index-url=https://pypi.org/simple",
    "--timeout=15",
    "--retries=1",
    "-r",
    str(root / requirements),
]
if retrieval:
    sys.argv.append("--extra-index-url=https://download.pytorch.org/whl/cpu")
for version in range(17, 37):
    sys.argv.append(f"--platform=manylinux_2_{version}_x86_64")
sys.argv.extend(["--platform=manylinux2014_x86_64", "--platform=linux_x86_64"])
try:
    runpy.run_module("pip", run_name="__main__")
except SystemExit as result:
    if result.code:
        raise
manifest = {
    p.name: hashlib.file_digest(p.open("rb"), "sha256").hexdigest() for p in target.glob("*.whl")
}
(target / "manifest.json").write_text(json.dumps(manifest, indent=2))
print({"wheel_count": len(manifest), "host_environment_modified": False})
