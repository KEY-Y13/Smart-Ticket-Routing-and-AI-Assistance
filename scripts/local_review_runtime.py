"""Reuse existing Ollama/qwen2.5:7b read-only, on a project-owned loopback port."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import psutil

from scripts.review_data_stage import ROOT, STAGE

EXE = Path(os.environ["LOCALAPPDATA"]) / "Programs/Ollama/ollama.exe"
STORE = Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama/models"))
MANIFEST = STORE / "manifests/registry.ollama.ai/library/qwen2.5/7b"
STATE = ROOT / ".mysql-local/ollama-review.json"
URL = "http://127.0.0.1:11435"


def assets():
    raw = MANIFEST.read_bytes()
    manifest = json.loads(raw)
    for layer in [manifest["config"], *manifest["layers"]]:
        path = STORE / "blobs" / layer["digest"].replace(":", "-")
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != layer["digest"].split(":")[1]:
                raise RuntimeError("Existing Ollama asset hash mismatch")
    return {
        "model": "qwen2.5:7b",
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "model_digest": manifest["layers"][0]["digest"],
        "existing_store": str(STORE),
        (
            "source"
        ): "Previously installed Ollama library artifact, all declared layer hashes verified",
        "license": "Existing model license layer retained",
        "store_modified_by_script": False,
    }


def owned(state):
    process = psutil.Process(state["pid"])
    if (
        Path(process.exe()).resolve() != EXE.resolve()
        or abs(process.create_time() - state["created"]) > 0.01
        or process.cmdline()[-1] != "serve"
    ):
        raise RuntimeError("Refusing a process outside this task")
    return process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    args = parser.parse_args()
    provenance = assets()
    if args.action == "stop":
        state = json.loads(STATE.read_text())
        process = owned(state)
        with httpx.Client(trust_env=False, timeout=120) as client:
            response = client.post(
                URL + "/api/generate", json={"model": "qwen2.5:7b", "keep_alive": 0}
            )
            response.raise_for_status()
        process.terminate()
        process.wait(20)
        STATE.write_text(json.dumps({"stopped": True, "assets_after": provenance}))
        print({"stopped_project_runtime": True, "existing_model_hashes_unchanged": True})
        return
    if any(
        conn.status == psutil.CONN_LISTEN and conn.laddr.port == 11435
        for conn in psutil.net_connections("tcp")
    ):
        if STATE.exists():
            owned(json.loads(STATE.read_text()))
            print({"already_running": URL})
            return
        raise RuntimeError("Port already belongs to an unrelated process")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SMARTTICKET_") and "API_KEY" not in key
    }
    env.update(
        OLLAMA_HOST="127.0.0.1:11435",
        OLLAMA_MODELS=str(STORE),
        OLLAMA_NO_CLOUD="1",
        OLLAMA_NUM_PARALLEL="4",
        OLLAMA_MAX_LOADED_MODELS="1",
        OLLAMA_FLASH_ATTENTION="1",
    )
    log = ROOT / "artifacts/data-research/ollama-local-review.log"
    with log.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            [str(EXE), "serve"],
            env=env,
            cwd=ROOT,
            stdout=handle,
            stderr=handle,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    STATE.write_text(
        json.dumps({"pid": process.pid, "created": psutil.Process(process.pid).create_time()})
    )
    with httpx.Client(trust_env=False, timeout=3) as client:
        for _ in range(120):
            try:
                response = client.get(URL + "/api/tags")
                if response.status_code == 200 and any(
                    model["name"] == "qwen2.5:7b" for model in response.json()["models"]
                ):
                    (STAGE / "qwen7b-existing-assets.json").write_text(
                        json.dumps(provenance, indent=2), encoding="utf-8"
                    )
                    print({"url": URL, "model": "qwen2.5:7b", "no_model_download": True})
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    raise RuntimeError("Project review runtime did not become ready")


if __name__ == "__main__":
    main()
