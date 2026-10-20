from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import bcrypt
from jose import JWTError, jwt

from .config import Settings

if TYPE_CHECKING:
    from .connectors.postgres import PostgresConnector




@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: str
    team: str
    allowed_services: tuple[str, ...] = ()
    allowed_source_apps: tuple[str, ...] = ()

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def as_dict(self) -> dict[str, Any]:
        return {
            "userId": self.user_id,
            "username": self.username,
            "role": self.role,
            "team": self.team,
            "allowedServices": list(self.allowed_services),
            "allowedSourceApps": list(self.allowed_source_apps),
        }


class AuthService:
    def __init__(self, settings: Settings, postgres: PostgresConnector) -> None:
        self.settings = settings
        self.postgres = postgres
        self.bootstrap_principal: Principal | None = None

    async def ensure_bootstrap_user(self) -> None:
        row = await self.postgres.upsert_user(
            username=self.settings.auth_bootstrap_username,
            password_hash=hash_password(self.settings.auth_bootstrap_password),
            role=self.settings.auth_bootstrap_role,
            team=self.settings.auth_bootstrap_team,
            allowed_services=self.settings.auth_bootstrap_service_list,
            allowed_source_apps=self.settings.auth_bootstrap_source_app_list,
            preserve_existing_password=True,
        )
        self.bootstrap_principal = principal_from_row(row)

    async def authenticate(self, username: str, password: str) -> Principal | None:
        row = await self.postgres.get_user(username)
        if not row or not row.get("is_active"):
            return None
        if not verify_password(password, str(row["password_hash"])):
            return None
        return principal_from_row(row)

    def issue_token(self, principal: Principal) -> tuple[str, datetime]:
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=self.settings.auth_token_minutes
        )
        payload = {
            "sub": principal.user_id,
            "username": principal.username,
            "role": principal.role,
            "team": principal.team,
            "services": list(principal.allowed_services),
            "source_apps": list(principal.allowed_source_apps),
            "exp": expires_at,
            "iat": datetime.now(timezone.utc),
        }
        return (
            jwt.encode(
                payload,
                self.settings.auth_jwt_secret,
                algorithm=self.settings.auth_jwt_algorithm,
            ),
            expires_at,
        )

    def decode_token(self, token: str) -> Principal:
        payload = jwt.decode(
            token,
            self.settings.auth_jwt_secret,
            algorithms=[self.settings.auth_jwt_algorithm],
        )
        return Principal(
            user_id=str(payload["sub"]),
            username=str(payload.get("username") or ""),
            role=str(payload.get("role") or "viewer"),
            team=str(payload.get("team") or ""),
            allowed_services=tuple(str(item) for item in payload.get("services") or []),
            allowed_source_apps=tuple(
                str(item) for item in payload.get("source_apps") or []
            ),
        )


class IdentityRateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self.limit = max(1, requests_per_minute)
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, identity: str, now: float) -> bool:
        async with self._lock:
            bucket = self._requests[identity]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


class AuthGatewayMiddleware:
    PUBLIC_PATHS = {"/health", "/api/auth/login"}

    def __init__(self, app: Any, auth: AuthService, settings: Settings) -> None:
        self.app = app
        self.auth = auth
        self.settings = settings
        self.limiter = IdentityRateLimiter(settings.auth_rate_limit_per_minute)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self.settings.auth_enabled:
            if self.auth.bootstrap_principal is None:
                await _json_response(
                    send, 503, {"detail": "Bootstrap principal is not ready"}
                )
                return
            scope.setdefault("state", {})["principal"] = self.auth.bootstrap_principal
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "")
        if method == "OPTIONS" or path in self.PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        token = _bearer_token(scope.get("headers") or [])
        try:
            principal = self.auth.decode_token(token) if token else None
        except JWTError:
            principal = None
        if principal is None:
            await _json_response(send, 401, {"detail": "Authentication required"})
            return
        from time import monotonic

        if not await self.limiter.allow(principal.user_id, monotonic()):
            await _json_response(
                send, 429, {"detail": "Per-user rate limit exceeded"}
            )
            return
        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)


def principal_from_scope(scope: dict[str, Any]) -> Principal:
    principal = scope.get("state", {}).get("principal")
    if not isinstance(principal, Principal):
        raise RuntimeError("Authenticated principal is missing")
    return principal


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def principal_from_row(row: dict[str, Any]) -> Principal:
    return Principal(
        user_id=str(row["user_id"]),
        username=str(row["username"]),
        role=str(row["role"]),
        team=str(row["team"]),
        allowed_services=tuple(row.get("allowed_services") or []),
        allowed_source_apps=tuple(row.get("allowed_source_apps") or []),
    )


def _bearer_token(headers: list[tuple[bytes, bytes]]) -> str:
    for name, value in headers:
        if name.lower() == b"authorization":
            decoded = value.decode(errors="ignore")
            if decoded.lower().startswith("bearer "):
                return decoded[7:].strip()
    return ""


async def _json_response(send: Any, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
