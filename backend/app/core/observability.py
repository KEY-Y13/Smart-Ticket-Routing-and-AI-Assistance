"""Bounded process counters and a small atomic worker heartbeat, without input bodies or secrets."""

import json
import math
import os
from collections import Counter, deque
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from uuid import uuid4


class RequestMetrics:
    def __init__(self, limit=1000):
        self.started = monotonic()
        self.lock = Lock()
        self.statuses = Counter()
        self.latencies = deque(maxlen=limit)

    def record(self, status, seconds):
        with self.lock:
            self.statuses[str(status)] += 1
            self.latencies.append(max(0, seconds * 1000))

    def snapshot(self):
        with self.lock:
            values = sorted(self.latencies)
            return {
                "uptime_seconds": max(0, monotonic() - self.started),
                "requests": sum(self.statuses.values()),
                "server_errors": sum(
                    n for status, n in self.statuses.items() if int(status) >= 500
                ),
                "status_counts": dict(self.statuses),
                "latency_samples": len(values),
                "p95_ms": values[math.ceil(len(values) * 0.95) - 1] if values else None,
                "scope": "API_PROCESS_SINCE_START; latency nearest-rank over last 1000 requests",
            }


class WorkerHeartbeat:
    def __init__(self, path):
        self.path = path
        self.run_id = uuid4().hex
        self.started_at = datetime.now(UTC).isoformat()
        self.success_at = None
        self.failures = 0

    def write(self, status, error=None):
        now = datetime.now(UTC).isoformat()
        if status == "ok":
            self.success_at, self.failures = now, 0
        elif status == "error":
            self.failures += 1
        value = {
            "schema_version": 1,
            "run_id": self.run_id,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": now,
            "last_success_at": self.success_at,
            "status": status,
            "consecutive_failures": self.failures,
            "error_type": type(error).__name__ if error else None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.run_id}-{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def heartbeat_status(path, stale_seconds, *, now=None):
    now = now or datetime.now(UTC)
    try:
        if path.stat().st_size > 16384:
            raise ValueError("oversized heartbeat")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["schema_version"] != 1 or data["status"] not in {
            "starting",
            "running",
            "ok",
            "error",
            "stopped",
        }:
            raise ValueError("invalid heartbeat")
        updated = datetime.fromisoformat(data["updated_at"])
        if updated.tzinfo is None:
            raise ValueError("missing timezone")
        age = (now - updated).total_seconds()
        if age < -5 or age > stale_seconds:
            return "degraded", "后台心跳过期或时钟异常", max(0, age)
        if data["status"] == "running" and data.get("last_success_at"):
            return "healthy", "后台正在处理新周期", max(0, age)
        if data["status"] != "ok":
            return "degraded", "后台尚未完成正常周期或最近周期失败", max(0, age)
        return "healthy", "后台最近周期已完成", max(0, age)
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown", "后台心跳缺失或格式无效", None
