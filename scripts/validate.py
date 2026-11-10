"""Run checks with logs and JUnit; do not change test expectations or suppress warnings."""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def source_fingerprint(project):
    roots = [
        "backend",
        "nlp",
        "rag",
        "scripts",
        "tests",
        "tests_mysql",
        "tests_ml",
        "alembic",
        "frontend/src",
        "frontend/e2e",
    ]
    entries = {}
    for relative in roots:
        for path in (project / relative).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".vue", ".js", ".css"}:
                entries[path.relative_to(project).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    for relative in (
        "pyproject.toml",
        "frontend/package-lock.json",
        "frontend/vite.config.js",
        "compose.yaml",
        "Dockerfile",
    ):
        path = project / relative
        entries[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    if Path(sys.prefix).resolve() != (project / ".conda").resolve():
        raise RuntimeError("请使用项目独立 Conda 环境执行检查")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = project / f"artifacts/validation/{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    checks = [
        ("dependencies", ["pip", "check"]),
        ("lint", ["ruff", "check", ".", "--exclude", ".conda"]),
        ("format", ["ruff", "format", "--check", ".", "--exclude", ".conda"]),
        ("tests", ["pytest", "-q", "--junitxml", str(output / "junit.xml")]),
    ]
    records = []
    source_before = source_fingerprint(project)
    for name, args in checks:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", *args],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        text = result.stdout + result.stderr
        (output / f"{name}.log").write_text(text, encoding="utf-8")
        print(text, flush=True)
        records.append({"name": name, "returncode": result.returncode, "args": args})
    source_after = source_fingerprint(project)
    records.append(
        {
            "name": "source_unchanged_during_validation",
            "returncode": int(source_before != source_after),
        }
    )
    (output / "checks.json").write_text(
        json.dumps(
            {
                "at": datetime.now(UTC).isoformat(),
                "python": sys.version,
                "checks": records,
                "source_sha256": source_after,
                "scope": (
                    "reference backend, RAG contracts and NLP units on temporary SQLite; "
                    "real MySQL/ML/retrieval/browser/deployment have separate reports"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"验证记录: {output}")
    return int(any(record["returncode"] for record in records))


if __name__ == "__main__":
    sys.exit(main())
