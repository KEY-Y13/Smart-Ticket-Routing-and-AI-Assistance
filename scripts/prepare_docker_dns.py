"""Optional Windows build-only DNS workaround; does not modify system/daemon DNS or TLS."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1]
hosts = [
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "registry.npmmirror.com",
    "download.pytorch.org",
    "download-r2.pytorch.org",
    "deb.debian.org",
]
addresses = {}
for host in hosts:
    command = (
        f"Resolve-DnsName '{host}' -Server 1.1.1.1 -Type A -DnsOnly "
        "-QuickTimeout -ErrorAction Stop | Where-Object IPAddress -Match '^\\d+\\.' "
        "| Select-Object -First 1 -ExpandProperty IPAddress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    address = result.stdout.strip()
    import ipaddress

    ipaddress.IPv4Address(address)
    addresses[host] = address
    print("Resolved", host, flush=True)
build = {
    "extra_hosts": addresses,
    "args": {
        name: ""
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        )
    },
}
override = {"services": {name: {"build": build} for name in ("api", "migrate", "retrieval")}}
target = root / "artifacts/docker-build-dns.json"
target.write_text(json.dumps(override, indent=2), encoding="utf-8")
(root / "artifacts/docker-build-dns-provenance.json").write_text(
    json.dumps(
        {
            "at": datetime.now(UTC).isoformat(),
            "resolver": "1.1.1.1",
            "scope": "temporary build /etc/hosts override only; TLS verification unchanged",
            "addresses": addresses,
        },
        indent=2,
    ),
    encoding="utf-8",
)
print(target)
