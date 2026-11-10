"""Package an explicit source allowlist; exclude keys, databases and environments."""

import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from backend.app.core.config import Settings
from scripts.provision_local_mysql import ROOT

DIRECTORIES = [
    "backend",
    "nlp",
    "rag",
    "scripts",
    "alembic",
    "docs",
    "tests",
    "tests_mysql",
    "tests_ml",
    "frontend/src",
    "frontend/e2e",
    "frontend/dist",
    "docker",
    "data/knowledge",
    "data/samples",
    "data/licenses",
]
FILES = [
    "README.md",
    "pyproject.toml",
    "alembic.ini",
    "Dockerfile",
    ".gitignore",
    ".dockerignore",
    ".env.example",
    "compose.yaml",
    "compose.offline.yaml",
    "compose.restore.yaml",
    "environment-win64.explicit.txt",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/vite.config.js",
    "frontend/playwright.config.js",
    "data/processed/README.md",
    "data/processed/classification-ai-v3.jsonl",
    "data/candidates/README.md",
]
EVIDENCE = [
    "artifacts/evaluation/classification-smoke.json",
    "artifacts/evaluation/retrieval-demo.json",
    "artifacts/evaluation/classification-formal.json",
    "artifacts/evaluation/retrieval-ai-v2.json",
    "artifacts/data-review-v3/summary.json",
    "artifacts/data-review-v3/reviewer-audit.json",
    "artifacts/data-review-v3/lineage.json",
    "artifacts/data-review-v3/snapshot.json",
    "artifacts/data-review-v3/split-manifest.json",
    "artifacts/data-review-v3/chinese-review-decisions.json",
    "artifacts/data-review-v3/provenance.json",
    "artifacts/data-review-v3/audit-cases.json",
    "artifacts/data-review-v3/paid-review-summary.json",
    "artifacts/data-review-v2/codex-business-review.json",
    "artifacts/data-review-v2/codex-native-72-review.json",
    "artifacts/data-review-v2/reply-renderer-v2-replay.json",
    "artifacts/data-review-v2/codex-chinese-v3-audit.json",
    "artifacts/evaluation/classification-domain-v3/report.json",
    "artifacts/evaluation/bailian-live-20261107T012904456529Z/report.json",
]


def release_paths(root):
    paths = []
    for relative in DIRECTORIES:
        directory = root / relative
        if not directory.is_dir():
            continue
        for base, folders, files in os.walk(directory):
            folders[:] = [
                name
                for name in folders
                if not name.startswith(".") and name not in {"__pycache__", "node_modules"}
            ]
            for name in files:
                path = Path(base) / name
                if path.suffix in {".pyc", ".pyo", ".key", ".pem"} or name.startswith(".env"):
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Release files cannot link outside the source root")
                paths.append(path)
    for relative in FILES + EVIDENCE:
        path = root / relative
        if path.is_file():
            if path.is_symlink():
                raise ValueError("Release entry is a link")
            paths.append(path)
    paths.extend(root.glob("requirements*.txt"))
    paths.extend(root.glob("requirements*.lock"))
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Release entries cannot link outside the root")
    return sorted(set(paths))


def main():
    settings = Settings(_env_file=ROOT / ".env")
    values = list(json.loads((ROOT / ".mysql-local/credentials.json").read_text()).values())
    for name in ("llm_api_key", "retrieval_secret", "jwt_secret"):
        value = getattr(settings, name)
        if value:
            values.append(value.get_secret_value())
    values.extend(
        path.read_text().strip() for path in (ROOT / ".docker-secrets").iterdir() if path.is_file()
    )
    sensitive = [value.encode() for value in values if isinstance(value, str) and len(value) >= 16]
    paths, manifest, contents = release_paths(ROOT), {}, {}
    for path in paths:
        data = path.read_bytes()
        if data.lstrip().startswith(b"-----BEGIN PRIVATE KEY-----") or any(
            value in data for value in sensitive
        ):
            raise RuntimeError(
                "Release packaging stopped: a configured secret or private key was found"
            )
        relative = path.relative_to(ROOT).as_posix()
        contents[relative] = data
        manifest[relative] = {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = ROOT / "artifacts/releases" / f"SmartTicket-AI-{stamp}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in contents.items():
            archive.writestr(relative, data)
        archive.writestr(
            "RELEASE-MANIFEST.json",
            json.dumps(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "scope": "source, web, fictional evidence; no keys, DBs, weights or envs",
                    "files": manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for relative, record in manifest.items():
            assert hashlib.sha256(archive.read(relative)).hexdigest() == record["sha256"]
    report = {
        "archive": str(output),
        "files": len(manifest),
        "bytes": output.stat().st_size,
        "archive_sha256": hashlib.file_digest(output.open("rb"), "sha256").hexdigest(),
        "all_members_hash_verified": True,
        "configured_secret_scan_passed": True,
        "environments_databases_keys_and_model_weights_included": False,
    }
    (ROOT / "artifacts/validation/release-package.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(report)


if __name__ == "__main__":
    main()
