"""Compare runtime code and web hashes with the running project image without printing config."""

import hashlib
import json
import subprocess

from scripts.provision_local_mysql import ROOT


def main():
    paths = []
    for relative in ("backend", "nlp", "rag", "scripts", "alembic", "frontend/dist"):
        paths.extend(
            path
            for path in (ROOT / relative).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    hashes = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.suffix not in {".pyc", ".pyo"}
    }
    code = (
        "import hashlib,json; from pathlib import Path; "
        "print(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in "
        + repr(list(hashes))
        + "}))"
    )
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    image = subprocess.check_output(
        ["docker", "image", "inspect", "smartticket-ai-app:1.0.0", "--format", "{{.Id}}"], text=True
    ).strip()
    matches = json.loads(result.stdout) == hashes
    report = {"image": image, "all_source_hashes_match": matches, "files": hashes}
    (ROOT / "artifacts/validation/final-docker-source.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print({"image": image, "files_checked": len(hashes), "all_source_hashes_match": matches})
    return int(not matches)


if __name__ == "__main__":
    raise SystemExit(main())
