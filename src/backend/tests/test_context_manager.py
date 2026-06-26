"""Tests for ContextManager session and memory handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from app.services.context_manager import ContextManager
from app.security import Principal


@pytest.mark.asyncio
async def test_context_manager_lifecycle() -> None:
    postgres = MagicMock()
    # Mock query results for create, get, list, load, persist
    postgres.query = AsyncMock(side_effect=[
        # create_session returns inserted session row
        [{"session_id": "sess-123", "user_id": "usr-1", "title": "Test Title", "memory": {}, "turns": []}],
        # get_session returns session row
        [{"session_id": "sess-123", "user_id": "usr-1", "title": "Test Title", "memory": {"theme": "sales"}, "turns": [{"question": "hi", "answer": "hello"}]}],
        # list_sessions returns list of sessions
        [{"session_id": "sess-123", "title": "Test Title", "updated_at": "2026-06-26T07:50:00Z"}],
        # load_context get_session query
        [{"session_id": "sess-123", "user_id": "usr-1", "title": "Test Title", "memory": {"last_table": "vsf_raw.orders"}, "turns": [{"question": "Show orders", "answer": "Here are orders", "sql": "SELECT 1"}]}],
        # persist_turn update session query
        [{"session_id": "sess-123", "memory": {"last_table": "vsf_raw.users"}, "turns": [{"question": "Show users", "answer": "Done"}]}],
    ])

    mgr = ContextManager(postgres=postgres)
    principal = Principal(user_id="usr-1", username="user1", role="editor", team="BI")

    # 1. create_session
    created = await mgr.create_session(principal=principal, title="Test Title")
    assert created["session_id"] == "sess-123"
    assert created["title"] == "Test Title"

    # 2. get_session
    session = await mgr.get_session(principal=principal, session_id="sess-123")
    assert session is not None
    assert session["session_id"] == "sess-123"
    assert session["memory"] == {"theme": "sales"}

    # 3. list_sessions
    sessions = await mgr.list_sessions(principal=principal, limit=10)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "sess-123"

    # 4. load_context
    memory, turns = await mgr.load_context(principal=principal, session_id="sess-123")
    assert memory == {"last_table": "vsf_raw.orders"}
    assert len(turns) == 1
    assert turns[0]["question"] == "Show orders"

    # 5. persist_turn
    updated = await mgr.persist_turn(
        principal=principal,
        session_id="sess-123",
        question="Show users",
        result={"answer": "Done", "sql": "SELECT 1 FROM vsf_raw.users", "selectedTables": [{"tableName": "vsf_raw.users"}]},
        previous_memory=memory,
    )
    assert updated["session_id"] == "sess-123"


@pytest.mark.asyncio
async def test_context_manager_none_session() -> None:
    postgres = MagicMock()
    mgr = ContextManager(postgres=postgres)
    principal = Principal(user_id="usr-1", username="user1", role="editor", team="BI")

    memory, turns = await mgr.load_context(principal=principal, session_id=None)
    assert memory == {}
    assert turns == []


@pytest.mark.asyncio
async def test_context_manager_postgres_error() -> None:
    postgres = MagicMock()
    postgres.query = AsyncMock(side_effect=Exception("Postgres connection failed"))

    mgr = ContextManager(postgres=postgres)
    principal = Principal(user_id="usr-1", username="user1", role="editor", team="BI")

    session = await mgr.get_session(principal=principal, session_id="sess-123")
    assert session is None

    sessions = await mgr.list_sessions(principal=principal, limit=10)
    assert sessions == []

    memory, turns = await mgr.load_context(principal=principal, session_id="sess-123")
    assert memory == {}
    assert turns == []

    # Persist turn on failure should still return a constructed fallback dict
    updated = await mgr.persist_turn(
        principal=principal,
        session_id="sess-123",
        question="Show users",
        result={"answer": "Done"},
        previous_memory={},
    )
    assert updated["session_id"] == "sess-123"
