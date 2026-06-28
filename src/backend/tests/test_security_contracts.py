from __future__ import annotations

from app.agents.row_security import apply_row_security, visible_tables
from app.config import Settings
from app.routers.query import build_router
from app.security import AuthGatewayMiddleware, Principal
from app.services.context_manager import ContextManager


class StubConnector:
    pass


def test_only_health_and_login_are_public() -> None:
    assert AuthGatewayMiddleware.PUBLIC_PATHS == {"/health", "/api/auth/login"}


def test_safe_router_owns_legacy_aliases() -> None:
    settings = Settings(table_selector_embedding_enabled=False)
    stub = StubConnector()
    router = build_router(
        settings,
        stub,  # type: ignore[arg-type]
        stub,  # type: ignore[arg-type]
        stub,  # type: ignore[arg-type]
        ContextManager(stub),  # type: ignore[arg-type]
    )
    paths = {route.path for route in router.routes}
    assert {"/chat", "/query", "/schema"} <= paths
    assert {"/api/agent/chat", "/api/agent/sql", "/api/agent/schema"} <= paths


def test_row_security_scopes_server_mart_source_app_id() -> None:
    principal = Principal(
        user_id="user-1",
        username="analyst",
        role="analyst",
        team="payments",
        allowed_source_apps=("PAYMENTS",),
    )
    secured = apply_row_security(
        (
            "SELECT source_app_id, sum(total_cost_usd) "
            "FROM vsf_mart.gold_app_daily_cost GROUP BY source_app_id"
        ),
        dialect="clickhouse",
        tables=[
            {
                "tableName": "vsf_mart.gold_app_daily_cost",
                "database": "vsf_mart",
                "columns": [
                    {"name": "source_app_id"},
                    {"name": "total_cost_usd"},
                ],
            }
        ],
        principal=principal,
    )
    assert "source_app_id IN ('PAYMENTS')" in secured


def test_non_admin_catalog_includes_server_analytics_layers() -> None:
    principal = Principal(
        user_id="user-1",
        username="analyst",
        role="analyst",
        team="payments",
    )
    tables = [
        {"tableName": "gold.old", "database": "gold"},
        {"tableName": "vsf_mart.current", "database": "vsf_mart"},
        {"tableName": "vsf_stg.detail", "database": "vsf_stg"},
        {"tableName": "vsf_raw.events", "database": "vsf_raw"},
        {"tableName": "ops.audit", "database": "ops"},
    ]
    visible = visible_tables(tables, principal)
    assert {table["database"] for table in visible} == {"vsf_mart", "vsf_raw"}
