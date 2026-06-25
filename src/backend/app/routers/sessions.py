from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..security import principal_from_scope
from ..services.context_manager import ContextManager


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=160)


def build_router(context: ContextManager) -> APIRouter:
    router = APIRouter(prefix="/api/query/chat/sessions", tags=["chat-sessions"])

    @router.post("")
    async def create(
        request: Request, payload: CreateSessionRequest
    ) -> dict[str, object]:
        session = await context.create_session(
            principal_from_scope(request.scope), payload.title
        )
        return {"session": session}

    @router.get("")
    async def list_sessions(request: Request, limit: int = 30) -> dict[str, object]:
        sessions = await context.list_sessions(
            principal_from_scope(request.scope), min(max(limit, 1), 100)
        )
        return {"sessions": sessions}

    @router.get("/{session_id}")
    async def get_session(request: Request, session_id: str) -> dict[str, object]:
        session = await context.get_session(
            principal_from_scope(request.scope), session_id
        )
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session": session}

    return router
