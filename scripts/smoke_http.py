"""Start a real loopback HTTP server on an ephemeral port, verify it, then stop it."""

import argparse
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from backend.app.core.config import Settings
from backend.app.main import create_app


def verify_http() -> dict:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(Settings(env="test")), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    responses = {}
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("本地 HTTP 服务未能启动")
            time.sleep(0.02)
        # Avoid inherited proxy settings for this explicitly loopback-only check.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path, expected in (
            ("/health/live", 200),
            ("/health/ready", 503),
            ("/openapi.json", 200),
        ):
            try:
                response = opener.open(f"http://127.0.0.1:{port}{path}", timeout=3)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status = response.code
                payload = json.load(response)
                if status != expected:
                    raise AssertionError(f"{path}: expected {expected}, got {status}")
                if path == "/health/live" and payload["status"] != "alive":
                    raise AssertionError("liveness 内容错误")
                if (
                    path == "/health/ready"
                    and payload["error"]["code"] != "DATABASE_NOT_CONFIGURED"
                ):
                    raise AssertionError("readiness 不应宣称业务已完成")
                responses[path] = status
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("测试 HTTP 服务未能退出")
    return {
        "at": datetime.now(UTC).isoformat(),
        "responses": responses,
        "server_stopped": True,
        "scope": "core_backend_unconfigured_http_only",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = verify_http()
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
