from __future__ import annotations

from time import monotonic
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..security import (
    AuthService,
    IdentityRateLimiter,
    hash_password,
    principal_from_scope,
)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=500)


class UserUpsertRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=12, max_length=500)
    role: Literal["admin", "analyst", "viewer"] = "viewer"
    team: str = Field(min_length=1, max_length=120)
    allowedServices: list[str] = Field(default_factory=list, max_length=100)
    allowedSourceApps: list[str] = Field(default_factory=list, max_length=100)


def build_router(auth: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])
    login_limiter = IdentityRateLimiter(
        auth.settings.auth_login_rate_limit_per_minute
    )

    @router.post("/login")
    async def login(payload: LoginRequest) -> dict[str, object]:
        if not await login_limiter.allow(payload.username.casefold(), monotonic()):
            raise HTTPException(
                status_code=429, detail="Login rate limit exceeded"
            )
        principal = await auth.authenticate(payload.username, payload.password)
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token, expires_at = auth.issue_token(principal)
        return {
            "accessToken": token,
            "tokenType": "bearer",
            "expiresAt": expires_at.isoformat(),
            "user": principal.as_dict(),
        }

    @router.get("/me")
    async def me(request: Request) -> dict[str, object]:
        return {"user": principal_from_scope(request.scope).as_dict()}

    @router.get("/users")
    async def users(request: Request, limit: int = 100) -> dict[str, object]:
        principal = principal_from_scope(request.scope)
        _require_admin(principal.is_admin)
        rows = await auth.postgres.list_users(limit)
        await auth.postgres.audit(
            {
                "query_id": uuid4().hex,
                "endpoint": "/api/auth/users",
                "request_type": "auth_admin",
                "question": "list users",
                "target": "dwh",
                "status": "success",
                "row_count": len(rows),
                "user_id": principal.user_id,
                "username": principal.username,
                "user_role": principal.role,
                "user_team": principal.team,
            }
        )
        return {"users": rows}

    @router.post("/users")
    async def upsert_user(
        request: Request, payload: UserUpsertRequest
    ) -> dict[str, object]:
        principal = principal_from_scope(request.scope)
        _require_admin(principal.is_admin)
        user = await auth.postgres.upsert_user(
            username=payload.username,
            password_hash=hash_password(payload.password),
            role=payload.role,
            team=payload.team,
            allowed_services=_clean_scope(payload.allowedServices),
            allowed_source_apps=_clean_scope(payload.allowedSourceApps),
        )
        await auth.postgres.audit(
            {
                "query_id": uuid4().hex,
                "endpoint": "/api/auth/users",
                "request_type": "auth_admin",
                "question": f"upsert user {payload.username}",
                "target": "dwh",
                "status": "success",
                "row_count": 1,
                "metadata": {
                    "managedUsername": payload.username,
                    "managedRole": payload.role,
                    "managedTeam": payload.team,
                },
                "user_id": principal.user_id,
                "username": principal.username,
                "user_role": principal.role,
                "user_team": principal.team,
            }
        )
        return {"user": user}

    return router


def _require_admin(is_admin: bool) -> None:
    if not is_admin:
        raise HTTPException(status_code=403, detail="Administrator role required")


def _clean_scope(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})
