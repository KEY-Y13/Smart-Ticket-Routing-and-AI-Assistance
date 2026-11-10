"""Explicitly authorized live evaluation through the app's durable LLM call budget."""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/evaluation"
FATAL = {"LLM_AUTHENTICATION_FAILED", "LLM_MODEL_UNAVAILABLE", "LLM_BUDGET_EXHAUSTED"}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def login(client, name, password):
    response = client.post("/api/v1/auth/login", json={"login": name, "password": password})
    response.raise_for_status()
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]


def summarize(directory, plan):
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("case-*.json"))
    ]
    success = [row for row in rows if row.get("status") == "DRAFT_CREATED"]
    durations = [row["latency_ms"] for row in rows]
    usage = {
        key: sum(row.get("usage", {}).get(key, 0) for row in rows)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")
    }
    # Published Beijing <=128K non-thinking list prices, not a provider bill.
    cached = min(usage["cached_tokens"], usage["prompt_tokens"])
    estimated = (
        (usage["prompt_tokens"] - cached) * 0.8 + cached * 0.16 + usage["completion_tokens"] * 2
    ) / 1_000_000
    report = {
        "at": datetime.now(UTC).isoformat(),
        "mode": "REAL_BAILIAN_CALLS_THROUGH_TICKET_API",
        "source": "AI_AUTHORED_FICTIONAL_CASES; NOT_INDEPENDENTLY_REVIEWED",
        "plan": plan,
        "attempted_cases": len(rows),
        "successful_drafts": len(success),
        "expected_document_selected": sum(
            row.get("expected_document_selected", False) for row in rows
        ),
        "all_created_drafts_pending_and_unsent": all(row["pending_and_unsent"] for row in success),
        "human_quality_review": "NOT_EXECUTED; no automatic acceptance feedback submitted",
        "provider_usage": usage,
        "published_rate_estimate_cny": estimated if success else None,
        "price_source": "https://help.aliyun.com/zh/model-studio/qwen-plus",
        "price_snapshot_verified_at": "2026-11-07",
        "price_scope": "Beijing <=128K input, non-thinking; excludes discounts; not actual billing",
        "calls_with_unknown_usage": sum(
            bool(row.get("invocations")) and not row.get("usage") for row in rows
        ),
        "p50_ms": float(np.percentile(durations, 50)) if durations else None,
        "p95_ms": float(np.percentile(durations, 95)) if durations else None,
        "completed_all_50_cases": len(rows) == 50,
        "results": rows,
    }
    write(directory / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1, choices=range(1, 51))
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    catalog_path = ROOT / "data/knowledge/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    directory = (
        args.run_dir.resolve()
        if args.run_dir
        else OUTPUT / ("bailian-live-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    )
    if not directory.resolve().is_relative_to(OUTPUT.resolve()):
        raise ValueError("Evaluation output must remain inside this project's artifacts/evaluation")
    directory.mkdir(parents=True, exist_ok=bool(args.run_dir))
    plan = {
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "cases": [
            {"id": i, "document_key": row[0], "question": row[3]}
            for i, row in enumerate(catalog["items"], 1)
        ],
        "expected_truth": "AI-authored matching fictional policy, not independently adjudicated",
        "automatic_retry": False,
    }
    if (directory / "plan.json").exists():
        if json.loads((directory / "plan.json").read_text(encoding="utf-8")) != plan:
            raise ValueError("Frozen evaluation plan changed")
    else:
        write(directory / "plan.json", plan)
    credentials = json.loads((ROOT / ".mysql-local/credentials.json").read_text())
    print({"run_dir": str(directory), "requested_case_limit": args.limit}, flush=True)
    with (
        httpx.Client(base_url="http://127.0.0.1:8010", trust_env=False, timeout=240) as admin,
        httpx.Client(base_url="http://127.0.0.1:8010", trust_env=False, timeout=30) as customer,
    ):
        login(admin, "admin", credentials["admin_password"])
        login(customer, "demo_customer_1", credentials["demo_password"])
        if admin.get("/api/v1/capabilities").json()["llm"] != "enabled":
            raise ValueError("Live evaluation requires explicit LLM enablement")
        documents = {}
        for page in range(1, 11):
            response = admin.get("/api/v1/knowledge", params={"page": page})
            response.raise_for_status()
            for doc in response.json():
                if doc["status"] == "PUBLISHED":
                    documents.setdefault(doc["document_key"], doc["id"])
            if len(response.json()) < 50:
                break
        for case in plan["cases"][: args.limit]:
            target = directory / f"case-{case['id']:02}.json"
            pending = directory / f"pending-{case['id']:02}.json"
            if target.exists():
                continue  # Never reissue an evaluated paid call on resume.
            if pending.exists():
                raise ValueError(
                    "Unknown prior call outcome; inspect durable invocation, no automatic retry"
                )
            created = customer.post(
                "/api/v1/tickets",
                json={
                    "subject": f"百炼真实评测{case['id']:02}：{case['question']}",
                    "body": case["question"],
                },
                headers={"Idempotency-Key": f"{directory.name}-{case['id']}"},
            )
            created.raise_for_status()
            ticket_id = created.json()["id"]
            write(pending, {"case_id": case["id"], "ticket_id": ticket_id})
            ticket = admin.get(f"/api/v1/tickets/{ticket_id}").json()
            started = time.perf_counter()
            result = admin.post(
                f"/api/v1/tickets/{ticket_id}/assistant/draft",
                json={"expected_version": ticket["version"], "mode": "llm"},
            )
            record = {
                **case,
                "ticket_id": ticket_id,
                "http_status": result.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
            if result.status_code == 201:
                draft = result.json()
                mapping = {row["chunk_id"]: row["document_id"] for row in draft["evidence"]}
                selected = {mapping[q["chunk_id"]] for q in draft["raw_output"]["quotes"]}
                record.update(
                    status="DRAFT_CREATED",
                    draft_id=draft["id"],
                    expected_document_selected=documents.get(case["document_key"]) in selected,
                    expected_document_retrieved=documents.get(case["document_key"])
                    in mapping.values(),
                    pending_and_unsent=draft["status"] == "PENDING_REVIEW"
                    and draft["sent_message_id"] is None,
                    raw_output=draft["raw_output"],
                    model_version=draft["model_version"],
                    usage=draft["usage"],
                    body=draft["body"],
                    evidence=draft["evidence"],
                )
            else:
                record.update(
                    status="APP_REJECTED", error_code=result.json().get("error", {}).get("code")
                )
            metrics = admin.get("/api/v1/metrics").json()
            record["invocations"] = [
                row for row in metrics["llm_invocations"] if row["ticket_id"] == ticket_id
            ]
            if "usage" not in record and record["invocations"]:
                record["usage"] = record["invocations"][0]["usage"]
            write(target, record)
            pending.rename(directory / f"request-{case['id']:02}.json")
            summarize(directory, plan)
            print(
                {"case": case["id"], "status": record["status"], "code": record.get("error_code")},
                flush=True,
            )
            if record.get("error_code") in FATAL:
                break
    report = summarize(directory, plan)
    print(
        {
            key: report[key]
            for key in ("attempted_cases", "successful_drafts", "completed_all_50_cases")
        }
    )
    return int(any(row["status"] != "DRAFT_CREATED" for row in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
