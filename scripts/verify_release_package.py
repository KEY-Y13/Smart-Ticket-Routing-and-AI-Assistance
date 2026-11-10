"""Extract an allowlisted archive into a new temporary directory and test its business code."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from scripts.provision_local_mysql import ROOT


def main():
    record = json.loads((ROOT / "artifacts/validation/release-package.json").read_text())
    archive_path = Path(record["archive"])
    if not archive_path.resolve().is_relative_to(ROOT / "artifacts/releases"):
        raise ValueError("Archive must belong to this project")
    checks = {
        "archive_hash_matches": hashlib.file_digest(archive_path.open("rb"), "sha256").hexdigest()
        == record["archive_sha256"]
    }
    with tempfile.TemporaryDirectory(prefix="smartticket-release-") as temporary:
        target = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                    raise ValueError("Unsafe archive member")
            manifest = json.loads(archive.read("RELEASE-MANIFEST.json"))["files"]
            checks["all_member_hashes_match"] = all(
                hashlib.sha256(archive.read(name)).hexdigest() == row["sha256"]
                for name, row in manifest.items()
            )
            archive.extractall(target)
        checks["no_private_directories"] = all(
            not (target / name).exists()
            for name in (
                ".env",
                ".mysql-local",
                ".docker-secrets",
                ".tls-local",
                ".conda",
                "frontend/node_modules",
            )
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("SMARTTICKET_")
            and key not in {"DASHSCOPE_API_KEY", "OPENAI_API_KEY", "PYTHONPATH"}
        }
        env.update(
            SMARTTICKET_ENV="test", SMARTTICKET_LLM_ENABLED="false", SMARTTICKET_LLM_CALL_BUDGET="0"
        )
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest", "tests/backend", "-q"],
            cwd=target,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        (ROOT / "artifacts/release-isolated-tests.log").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        checks["isolated_business_tests_pass"] = result.returncode == 0
    report = {
        "at": datetime.now(UTC).isoformat(),
        "archive": str(archive_path),
        "scope": "extracted source; SQLite; existing Python deps; not clean-machine install",
        "checks": checks,
        "passed": all(checks.values()),
    }
    (ROOT / "artifacts/validation/release-package-verification.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print({"passed": report["passed"], "checks": checks})
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
