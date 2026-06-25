from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_allowlist(raw: str) -> tuple[tuple[str, ...], ...]:
    commands: list[tuple[str, ...]] = []
    for item in raw.split(","):
        parts = tuple(part for part in item.strip().split(" ") if part)
        if parts:
            commands.append(parts)
    return tuple(commands)


class Settings(BaseModel):
    service_name: str = os.getenv("OTEL_SERVICE_NAME", "agentic-bi-observation-service")
    environment: str = os.getenv("OBSERVATION_ENVIRONMENT", "server")
    source_app_id: str = os.getenv("SOURCE_APP_ID", "agentic-bi")
    repo_root: Path = Path(os.getenv("OBSERVATION_REPO_ROOT", "/workspace")).resolve()
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    otlp_endpoint: str | None = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None
    langfuse_public_key: str | None = os.getenv("LANGFUSE_PUBLIC_KEY") or None
    langfuse_secret_key: str | None = os.getenv("LANGFUSE_SECRET_KEY") or None
    langfuse_host: str = os.getenv("LANGFUSE_HOST", "http://langfuse-web:3000")

    terminal_timeout_seconds: int = int(os.getenv("OBSERVATION_TERMINAL_TIMEOUT_SECONDS", "5"))
    terminal_max_output_chars: int = int(os.getenv("OBSERVATION_TERMINAL_MAX_OUTPUT_CHARS", "12000"))
    terminal_allowlist: tuple[tuple[str, ...], ...] = _parse_allowlist(
        os.getenv(
            "OBSERVATION_TERMINAL_ALLOWLIST",
            "date,whoami,pwd,ls,git status,python --version,python -V",
        )
    )

    clickhouse_host: str = os.getenv("CLICKHOUSE_HOST", "clickhouse")
    clickhouse_port: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    clickhouse_user: str = os.getenv("CLICKHOUSE_USER", "default")
    clickhouse_password: str = os.getenv("CLICKHOUSE_PASSWORD", "securepassword")
    clickhouse_secure: bool = os.getenv("CLICKHOUSE_SECURE", "false").lower() == "true"
    clickhouse_database: str = os.getenv("CLICKHOUSE_DATABASE", "default")
    clickhouse_analytics_schemas: str = os.getenv(
        "CLICKHOUSE_ANALYTICS_SCHEMAS", "gold,silver,vsf_mart,vsf_stg"
    )
    clickhouse_query_timeout_seconds: float = float(
        os.getenv("CLICKHOUSE_QUERY_TIMEOUT_SECONDS", "60")
    )
    query_timeout_seconds: int = int(os.getenv("OBSERVATION_QUERY_TIMEOUT_SECONDS", "60"))

    dwh_postgres_dsn: str = os.getenv(
        "DWH_POSTGRES_DSN",
        "postgresql://dwh_admin:dwh_admin_password@postgres-dwh:5432/central_dwh",
    )
    postgres_query_timeout_seconds: float = float(
        os.getenv("POSTGRES_QUERY_TIMEOUT_SECONDS", "60")
    )
    datahub_gms_url: str = os.getenv("DATAHUB_GMS_URL", "http://datahub-gms:8080")
    datahub_token: str = os.getenv("DATAHUB_TOKEN", "")
    datahub_timeout_seconds: float = float(os.getenv("DATAHUB_TIMEOUT_SECONDS", "15"))
    metabase_url: str = os.getenv("METABASE_URL", "http://metabase:3000")
    metabase_public_url: str = os.getenv("METABASE_PUBLIC_URL", "http://localhost:3000")
    metabase_admin_email: str = os.getenv("METABASE_ADMIN_EMAIL", "")
    metabase_admin_password: str = os.getenv("METABASE_ADMIN_PASSWORD", "")
    cors_origins: str = os.getenv(
        "CORS_ORIGINS", "http://localhost:3003,http://127.0.0.1:3003"
    )
    max_query_rows: int = int(os.getenv("OBSERVATION_MAX_QUERY_ROWS", "500"))
    agent_retry_count: int = int(os.getenv("OBSERVATION_AGENT_RETRY_COUNT", "2"))
    agent_synthesis_timeout_seconds: float = float(
        os.getenv("OBSERVATION_AGENT_SYNTHESIS_TIMEOUT_SECONDS", "120")
    )
    agent_sql_candidate_count: int = int(os.getenv("OBSERVATION_SQL_CANDIDATE_COUNT", "3"))

    llm_provider: str = os.getenv("LLM_PROVIDER", "openrouter").lower()
    llm_model: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b:free")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
    llm_sql_timeout_seconds: float = float(os.getenv("LLM_SQL_TIMEOUT_SECONDS", "90"))
    llm_sql_max_retries: int = int(os.getenv("LLM_SQL_MAX_RETRIES", "1"))
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_site_url: str = os.getenv("OPENROUTER_SITE_URL", "http://localhost:3003")
    openrouter_app_name: str = os.getenv("OPENROUTER_APP_NAME", "Agentic BI Monitoring")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    table_selector_embedding_enabled: bool = (
        os.getenv("TABLE_SELECTOR_EMBEDDING_ENABLED", "true").lower() == "true"
    )
    table_selector_embedding_timeout_seconds: float = float(
        os.getenv("TABLE_SELECTOR_EMBEDDING_TIMEOUT_SECONDS", "8")
    )
    disabled_analytics_tables: str = os.getenv("DISABLED_ANALYTICS_TABLES", "")

    auth_enabled: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    auth_jwt_secret: str = os.getenv(
        "AUTH_JWT_SECRET", "local-development-secret-change-me-64-characters-minimum"
    )
    auth_jwt_algorithm: str = os.getenv("AUTH_JWT_ALGORITHM", "HS256")
    auth_token_minutes: int = int(os.getenv("AUTH_TOKEN_MINUTES", "480"))
    auth_bootstrap_username: str = os.getenv("AUTH_BOOTSTRAP_USERNAME", "admin")
    auth_bootstrap_password: str = os.getenv("AUTH_BOOTSTRAP_PASSWORD", "change_me")
    auth_bootstrap_role: str = os.getenv("AUTH_BOOTSTRAP_ROLE", "admin")
    auth_bootstrap_team: str = os.getenv("AUTH_BOOTSTRAP_TEAM", "platform")
    auth_bootstrap_services: str = os.getenv("AUTH_BOOTSTRAP_SERVICES", "")
    auth_bootstrap_source_apps: str = os.getenv("AUTH_BOOTSTRAP_SOURCE_APPS", "")
    auth_rate_limit_per_minute: int = int(os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "120"))
    auth_login_rate_limit_per_minute: int = int(
        os.getenv("AUTH_LOGIN_RATE_LIMIT_PER_MINUTE", "10")
    )
    agent_daily_budget_usd: float = float(os.getenv("AGENT_DAILY_BUDGET_USD", "5"))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()

    @property
    def cors_origin_list(self) -> list[str]:
        return _csv(self.cors_origins)

    @property
    def clickhouse_analytics_schema_list(self) -> list[str]:
        return _csv(self.clickhouse_analytics_schemas)

    @property
    def llm_configured(self) -> bool:
        return bool(
            (self.llm_provider == "openrouter" and self.openrouter_api_key)
            or (self.llm_provider == "openai" and self.openai_api_key)
        )

    @property
    def auth_bootstrap_service_list(self) -> list[str]:
        return _csv(self.auth_bootstrap_services)

    @property
    def auth_bootstrap_source_app_list(self) -> list[str]:
        return _csv(self.auth_bootstrap_source_apps)

    @property
    def disabled_analytics_table_list(self) -> list[str]:
        return [item.lower() for item in _csv(self.disabled_analytics_tables)]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
