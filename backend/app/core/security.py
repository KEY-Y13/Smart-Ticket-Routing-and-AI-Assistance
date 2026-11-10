from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from backend.app.core.config import Settings
from backend.app.core.errors import AppError

passwords = PasswordHash.recommended()
_dummy_hash = passwords.hash("not-a-real-user-password")


def hash_password(password: str) -> str:
    return passwords.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    valid = passwords.verify(password, hashed or _dummy_hash)
    return valid and hashed is not None


def create_token(user, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "ver": user.auth_version,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=settings.token_minutes),
            "jti": uuid4().hex,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_token(token: str, settings: Settings) -> dict:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "ver", "iss", "aud", "iat", "nbf", "exp", "jti"]},
        )
        if not claims["sub"].isdigit() or not 0 < int(claims["sub"]) < 2**63:
            raise ValueError("invalid subject")
        if type(claims["ver"]) is not int:
            raise ValueError("invalid version")
        return claims
    except (jwt.InvalidTokenError, ValueError, AttributeError, TypeError) as exc:
        raise AppError(401, "INVALID_TOKEN", "登录已失效，请重新登录") from exc


class LoginLimiter:
    """Single API process limiter; not a distributed rate limit guarantee."""

    def __init__(self, limit=10, window=60, max_addresses=10000):
        self.limit, self.window = limit, window
        self.max_addresses = max_addresses
        self.entries = defaultdict(deque)
        self.lock = Lock()

    def check(self, address: str):
        now = monotonic()
        with self.lock:
            if address not in self.entries and len(self.entries) >= self.max_addresses:
                self.entries = defaultdict(
                    deque,
                    {k: v for k, v in self.entries.items() if v and v[-1] > now - self.window},
                )
                if len(self.entries) >= self.max_addresses:
                    raise AppError(429, "LOGIN_RATE_LIMIT", "登录请求过多，请稍后重试")
            queue = self.entries[address]
            while queue and queue[0] <= now - self.window:
                queue.popleft()
            if len(queue) >= self.limit:
                raise AppError(429, "LOGIN_RATE_LIMIT", "登录请求过多，请稍后重试")
            queue.append(now)
