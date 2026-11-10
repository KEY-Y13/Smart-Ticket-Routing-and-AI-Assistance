"""Inspect this environment without database connections, model downloads or API calls."""

import argparse
import importlib
import importlib.metadata
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

PACKAGES = {
    "fastapi": "fastapi",
    "pydantic": "pydantic",
    "pydantic-settings": "pydantic_settings",
    "numpy": "numpy",
    "scipy": "scipy",
    "scikit-learn": "sklearn",
    "joblib": "joblib",
    "pytest": "pytest",
    "httpx": "httpx",
    "uvicorn": "uvicorn",
}


def inspect_environment() -> dict:
    project = Path(__file__).resolve().parents[1]
    if Path(sys.prefix).resolve() != (project / ".conda").resolve():
        raise RuntimeError("请使用项目 .conda/python.exe，不能复用或修改其他环境")
    versions = {}
    for distribution, module in PACKAGES.items():
        importlib.import_module(module)
        versions[distribution] = importlib.metadata.version(distribution)
    return {
        "at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "prefix": sys.prefix,
        "imports": versions,
        "database_connection": "not_attempted",
        "gpu_runtime": "not_tested",
        "pretrained_models": "not_downloaded",
        "paid_api_calls": "not_attempted",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = inspect_environment()
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
