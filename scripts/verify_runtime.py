"""Read-only runtime/security checks. Secrets are read locally and never written to reports."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--expect-llm", choices=["disabled", "enabled"], default="disabled")
    args = parser.parse_args()
    label = "docker" if args.docker else "local"
    base = "http://127.0.0.1:" + ("8012" if args.docker else "8010")
    password = (
        (ROOT / ".docker-secrets/admin_password").read_text().strip()
        if args.docker
        else json.loads((ROOT / ".mysql-local/credentials.json").read_text())["admin_password"]
    )
    report = {
        "at": datetime.now(UTC).isoformat(),
        "url": base,
        "scope": "read-only core/auth/retrieval/security; synthetic local data",
        "checks": {},
    }
    checks = report["checks"]
    try:
        with httpx.Client(base_url=base, trust_env=False, timeout=120) as client:
            checks["ready"] = client.get("/health/ready").status_code == 200
            page = client.get("/")
            checks["web"] = page.status_code == 200 and "SmartTicket" in page.text
            checks["csp"] = "frame-ancestors 'none'" in page.headers.get(
                "content-security-policy", ""
            )
            checks["credentials_not_served"] = all(
                client.get(path).status_code == 404
                for path in (
                    "/.env",
                    "/.mysql-local/credentials.json",
                    "/.docker-secrets/admin_password",
                )
            )
            checks["anonymous_denied"] = client.get("/api/v1/tickets").status_code == 401
            login = client.post("/api/v1/auth/login", json={"login": "admin", "password": password})
            login.raise_for_status()
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            capabilities = client.get("/api/v1/capabilities").json()
            checks["llm_state_matches_expectation"] = capabilities["llm"] == args.expect_llm
            models = client.get("/api/v1/admin/model-reports").json()
            checks["four_classification_reports"] = (
                len(models.get("classification", {}).get("models", [])) == 4
            )
            checks["four_retrieval_reports"] = (
                len(models.get("retrieval", {}).get("strategies", [])) == 4
            )
            result = client.post(
                "/api/v1/knowledge/search",
                json={"query": "蓝牙耳机一侧没声音先查什么？", "strategy": "rerank"},
            )
            result.raise_for_status()
            data = result.json()
            checks["real_reranker"] = (
                data["sufficient"]
                and data["items"][0]["title"] == "耳机无声排查"
                and data["items"][0]["rerank_score"] is not None
            )
            report["retrieval"] = {
                "generation": data["generation"],
                "latency_ms": data["latency_ms"],
                "first_title": data["items"][0]["title"] if data["items"] else None,
            }
            metrics = client.get("/api/v1/metrics").json()
            if args.expect_llm == "disabled":
                checks["no_paid_invocations"] = metrics["llm_invocations"] == []
            acceptance = metrics["ai_acceptance"]
            expected_rate = (
                acceptance["numerator"] / acceptance["denominator"]
                if acceptance["denominator"]
                else None
            )
            checks["llm_acceptance_denominator_consistent"] = acceptance["rate"] == expected_rate
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            report["error_status"] = exc.response.status_code
            report["error_path"] = exc.request.url.path
            try:
                report["error_code"] = exc.response.json().get("error", {}).get("code")
            except ValueError:
                pass
    report["passed"] = bool(checks) and all(checks.values()) and "error_type" not in report
    target = ROOT / f"artifacts/validation/{label}-runtime.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print({"report": str(target), "passed": report["passed"], "checks": checks})
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
