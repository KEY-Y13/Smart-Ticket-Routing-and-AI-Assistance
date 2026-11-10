"""Read-only final review-stage evidence; never approves data, deploys models or calls an LLM."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import psutil
from sqlalchemy import func, select, text

from backend.app.core.config import Settings
from backend.app.db.models import (
    AIInvocation,
    ModelRelease,
    ReviewDecision,
    ReviewItem,
    ReviewSnapshot,
)
from backend.app.db.runtime import Database
from scripts.local_services import SERVICES, STATE, owned

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "artifacts/validation"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def scan_configured_secrets(settings):
    private = ROOT / ".mysql-local/credentials.json"
    values = list(read_json(private).values())
    for name in ("jwt_secret", "llm_api_key", "retrieval_secret"):
        value = getattr(settings, name)
        if value:
            values.append(value.get_secret_value())
    values.extend(
        p.read_text(encoding="utf-8").strip()
        for p in (ROOT / ".docker-secrets").iterdir()
        if p.is_file()
    )
    values = [value for value in values if isinstance(value, str) and len(value) >= 16]
    matches, scanned = [], 0
    suffixes = {
        ".py",
        ".js",
        ".vue",
        ".md",
        ".json",
        ".jsonl",
        ".log",
        ".txt",
        ".yaml",
        ".sql",
        ".html",
    }
    for base, folders, files in os.walk(ROOT):
        folders[:] = [
            name
            for name in folders
            if not name.startswith(".")
            and name
            not in {
                "node_modules",
                "__pycache__",
                "pretrained",
                "wheels-linux",
                "wheels-retrieval-linux",
            }
        ]
        for name in files:
            path = Path(base) / name
            if (
                name.startswith(".")
                or path.suffix not in suffixes
                or path.stat().st_size > 5_000_000
            ):
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            scanned += 1
            if any(value in content for value in values):
                matches.append(path.relative_to(ROOT).as_posix())
    return {
        "scope": "exact secret matches in text <=5MB; private/env/runtime dirs excluded",
        "files_scanned": scanned,
        "matched_files": matches,
    }


def main():
    settings = Settings(_env_file=ROOT / ".env")
    database = Database(settings)
    with database.sessions() as session:
        assert session.scalar(text("SELECT DATABASE()")) == "smartticket_ai"
        assert session.scalar(text("SELECT @@port")) == 33307
        statuses = dict(
            session.execute(
                select(ReviewItem.status, func.count()).group_by(ReviewItem.status)
            ).all()
        )
        counts = {
            "items": sum(statuses.values()),
            "statuses": statuses,
            "decisions": session.scalar(select(func.count()).select_from(ReviewDecision)),
            "snapshots": session.scalar(select(func.count()).select_from(ReviewSnapshot)),
            "releases": session.scalar(select(func.count()).select_from(ModelRelease)),
        }
        calls = session.scalar(select(func.count()).select_from(AIInvocation))
        version = session.scalar(text("SELECT version_num FROM alembic_version"))
        grants = {}
        for table, permission in session.execute(
            text(
                "SELECT TABLE_NAME,PRIVILEGE_TYPE FROM information_schema.TABLE_PRIVILEGES "
                "WHERE TABLE_SCHEMA=DATABASE()"
            )
        ):
            grants.setdefault(table, set()).add(permission)
        append = {"review_collections", "review_decisions", "review_snapshots"}
        source = {"orders", "products", "order_items", "payments", "shipments", "refunds"}
        rights_ok = all(grants.get(table) == {"SELECT", "INSERT"} for table in append)
        rights_ok &= all(grants.get(table) == {"SELECT"} for table in source)
    database.dispose()
    latest = max(REPORTS.glob("*/checks.json"), key=lambda path: path.stat().st_mtime)
    qa = Path.home() / ".codex/visualizations/smartticket-qa"
    records = read_json(STATE)
    local_services = {
        name: bool(
            records.get(name)
            and psutil.pid_exists(records[name])
            and owned(
                psutil.Process(records[name]),
                exe,
                args,
            )
        )
        for name, (exe, args, _) in SERVICES.items()
    }
    originals = read_json(REPORTS / "review-docker-before.json")
    restored_ids = set(
        (ROOT / "artifacts/review-docker-originals-restored.log").read_text().split()
    )
    secrets = scan_configured_secrets(settings)
    checks = {
        "default_regression": all(row["returncode"] == 0 for row in read_json(latest)["checks"]),
        "seven_mysql_tests": "7 passed"
        in (ROOT / "artifacts/review-mysql-tests-final.log").read_text(),
        "local_browser_review_train_shadow_revoke": read_json(qa / "review-qa.json")["passed"],
        "docker_browser_four_review_types": read_json(qa / "review-docker-qa.json")["passed"],
        "docker_runtime_sources_match": read_json(REPORTS / "review-docker-source.json")[
            "all_source_hashes_match"
        ],
        "docker_persistence_and_readonly_worker": read_json(
            REPORTS / "docker-restart-persistence.json"
        )["passed"],
        "docker_35_table_restore_application": read_json(REPORTS / "docker-volume-restore.json")[
            "passed"
        ],
        "local_35_table_restore": read_json(REPORTS / "backup-restore.json")[
            "all_table_counts_and_hashes_match"
        ],
        "local_runtime": read_json(REPORTS / "local-runtime.json")["passed"],
        "main_candidates_remain_unreviewed": counts
        == {
            "items": 282,
            "statuses": {"PENDING": 282},
            "decisions": 0,
            "snapshots": 0,
            "releases": 0,
        },
        "local_review_grants": rights_ok,
        "migration_at_head": version == "0003_reviews",
        "paid_calls_unchanged_54": calls == 54,
        "bounded_bailian_configuration": settings.llm_enabled
        and settings.llm_call_budget == 60
        and settings.llm_model == "qwen-plus",
        "local_services_running": all(local_services.values()),
        "original_container_startups_restored": len(originals) == 27
        and all(row["id"] in restored_ids for row in originals if row["state"] == "running"),
        "desktop_closed": not any(
            p.info["name"].lower() in {"docker desktop.exe", "com.docker.backend.exe"}
            for p in psutil.process_iter(["name"])
            if p.info["name"]
        ),
        "no_configured_plaintext_secret_matches": not secrets["matched_files"],
    }
    report = {
        "at": datetime.now(UTC).isoformat(),
        "scope": "review software stage; independent human review remains outstanding",
        "url": "http://127.0.0.1:8010",
        "checks": checks,
        "passed": all(checks.values()),
        "main_review": counts,
        "default_checks": str(latest.relative_to(ROOT)),
        "paid_budget": {
            "reserved": calls,
            "total": settings.llm_call_budget,
            "remaining": settings.llm_call_budget - calls,
        },
        "local_services": local_services,
        "secret_scan": secrets,
        "formal_model_deployed": False,
        "independent_human_review_completed": False,
    }
    target = REPORTS / "review-delivery.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print({"report": str(target), "passed": report["passed"], "checks": checks})
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
