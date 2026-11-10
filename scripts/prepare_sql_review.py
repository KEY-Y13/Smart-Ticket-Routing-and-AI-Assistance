"""Organize unexecuted review drafts and render initial configuration without DB access."""

from pathlib import Path

from backend.app.db.bootstrap import INITIAL_SLA

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    archive = root / "artifacts/sql-drafts"
    archive.mkdir(parents=True, exist_ok=True)
    pairs = [
        ("docs/sql/0001-core-review.sql", "artifacts/sql-drafts/0001-core-review.sql"),
        ("docs/sql/0001-core-review-final.sql", "artifacts/sql-drafts/0001-core-review-final.sql"),
        ("docs/sql/0001-core-review-approved-scope.sql", "docs/sql/01-core-schema-review.sql"),
    ]
    for before, after in pairs:
        source, target = (root / before).resolve(), (root / after).resolve()
        if not source.is_relative_to(root) or not target.is_relative_to(root):
            raise ValueError("审查文件只能在项目内整理")
        if source.exists():
            if target.exists():
                raise FileExistsError(target)
            source.rename(target)
    target = root / "docs/sql/02-initial-configuration-review.sql"
    lines = [
        "-- REVIEW ONLY; NOT EXECUTED. Use only a dedicated newly migrated database.",
        "INSERT INTO dispatch_mutex (id) VALUES (1);",
    ]
    for priority, response, resolution, warning in INITIAL_SLA:
        lines.append(
            "INSERT INTO sla_policies (policy_code, version, priority, response_seconds, "
            "resolution_seconds, warning_seconds, active, created_at) VALUES "
            f"('initial-natural-hours', 1, {priority}, {response}, {resolution}, {warning}, "
            "1, UTC_TIMESTAMP(6));"
        )
    with target.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("Review files prepared. No database connection was opened.")
