"""Verify real TLS chain and hostnames, keeping certificate validation and system trust intact."""

import json
import socket
import ssl
from datetime import UTC, datetime

import httpx

from scripts.local_https import PORT, ROOT, STATE


def main():
    state = json.loads(STATE.read_text())
    context = ssl.create_default_context(cafile=state["ca"])
    report = {
        "at": datetime.now(UTC).isoformat(),
        "scope": "PROJECT_LOCAL_CA_HTTPS_ONLY; NOT_PUBLIC_CA_OR_INTERNET_DEPLOYMENT",
        "checks": {},
    }
    checks = report["checks"]
    with socket.create_connection(("127.0.0.1", PORT), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname="localhost") as secured:
            report["tls_version"] = secured.version()
            checks["trusted_ca_and_localhost_verified"] = secured.version() in {
                "TLSv1.2",
                "TLSv1.3",
            }
    for name, client_context, hostname in (
        ("untrusted_ca_rejected", ssl.create_default_context(), "localhost"),
        ("wrong_hostname_rejected", context, "wrong.invalid"),
    ):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=5) as connection:
                with client_context.wrap_socket(connection, server_hostname=hostname):
                    checks[name] = False
        except ssl.SSLCertVerificationError:
            checks[name] = True
    private = json.loads((ROOT / ".mysql-local/credentials.json").read_text())
    with httpx.Client(
        base_url=f"https://127.0.0.1:{PORT}", verify=context, trust_env=False, timeout=6
    ) as client:
        checks["ip_san_and_ready_verified"] = client.get("/health/ready").status_code == 200
        page = client.get("/")
        checks["web_and_csp"] = (
            page.status_code == 200
            and "frame-ancestors 'none'" in page.headers.get("content-security-policy", "")
        )
        checks["anonymous_denied"] = client.get("/api/v1/tickets").status_code == 401
        login = client.post(
            "/api/v1/auth/login", json={"login": "admin", "password": private["admin_password"]}
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        data = client.get("/api/v1/admin/operations/health").json()
        checks["authenticated_health_over_tls"] = len(data["components"]) == 4
        checks["paid_budget_unchanged"] = data["llm_budget"]["reserved"] == 54
    report["passed"] = all(checks.values())
    target = ROOT / "artifacts/validation/local-https.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({"report": str(target), "passed": report["passed"], "checks": checks})
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
