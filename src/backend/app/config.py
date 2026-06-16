from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    service_name: str
    environment: str
    source_app_id: str
    repo_root: Path
    terminal_timeout_seconds: int
    terminal_max_output_chars: int
    terminal_allowlist: tuple[tuple[str, ...], ...]
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_secure: bool
    query_timeout_seconds: int
    datahub_gms_url: str
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_host: str
    otlp_endpoint: str | None
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("OBSERVATION_REPO_ROOT", "/workspace")).resolve()
        allowlist = _parse_allowlist(
            os.getenv(
                "OBSERVATION_TERMINAL_ALLOWLIST",
                "date,whoami,pwd,ls,git status,python --version,python -V",
            )
        )
        return cls(
            service_name=os.getenv("OTEL_SERVICE_NAME", "agentic-bi-observation-service"),
            environment=os.getenv("OBSERVATION_ENVIRONMENT", "local"),
            source_app_id=os.getenv("SOURCE_APP_ID", "agentic-bi-terminal-agent"),
            repo_root=root,
            terminal_timeout_seconds=_int_env("OBSERVATION_TERMINAL_TIMEOUT_SECONDS", 5),
            terminal_max_output_chars=_int_env("OBSERVATION_TERMINAL_MAX_OUTPUT_CHARS", 12000),
            terminal_allowlist=allowlist,
            clickhouse_host=os.getenv("CLICKHOUSE_HOST", "clickhouse"),
            clickhouse_port=_int_env("CLICKHOUSE_PORT", 8123),
            clickhouse_user=os.getenv("CLICKHOUSE_USER", "default"),
            clickhouse_password=os.getenv("CLICKHOUSE_PASSWORD", "securepassword"),
            clickhouse_secure=_bool_env("CLICKHOUSE_SECURE", False),
            query_timeout_seconds=_int_env("OBSERVATION_QUERY_TIMEOUT_SECONDS", 10),
            datahub_gms_url=os.getenv("DATAHUB_GMS_URL", "http://datahub-gms:8080"),
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY") or None,
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY") or None,
            langfuse_host=os.getenv("LANGFUSE_HOST", "http://langfuse-web:3000"),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


def _parse_allowlist(raw: str) -> tuple[tuple[str, ...], ...]:
    commands: list[tuple[str, ...]] = []
    for item in raw.split(","):
        parts = tuple(part for part in item.strip().split(" ") if part)
        if parts:
            commands.append(parts)
    return tuple(commands)
