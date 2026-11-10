from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class SLAPolicy:
    policy_version: str
    response_seconds: int
    resolution_seconds: int
    warning_seconds: int

    def __post_init__(self):
        if self.response_seconds <= 0 or self.resolution_seconds < self.response_seconds:
            raise ValueError("SLA 时限必须为正数且解决时限不能短于响应时限")
        if not 0 <= self.warning_seconds < self.response_seconds:
            raise ValueError("提醒提前量必须小于响应时限且非负")


def deadlines(started_at: datetime, policy: SLAPolicy) -> dict[str, datetime]:
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("SLA 开始时间必须包含时区")
    return {
        "response_due_at": started_at + timedelta(seconds=policy.response_seconds),
        "resolution_due_at": started_at + timedelta(seconds=policy.resolution_seconds),
    }
