from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_observation_service_exposes_plan_endpoints():
    app_code = read("src/backend/app/main.py") + read("src/backend/app/routers/query.py")

    for route in ('"/chat"', '"/terminal/run"', '"/query"', '"/schema"'):
        assert route in app_code


def test_terminal_api_is_allowlisted_and_shell_free():
    terminal = read("src/backend/app/terminal.py")
    config = read("src/backend/app/config.py")

    assert "shell=False" in terminal
    assert "OBSERVATION_TERMINAL_ALLOWLIST" in config
    assert "Command is not allowlisted" in terminal
    assert "Shell control syntax is not allowed" in terminal
    assert "Working directory must stay inside the repository" in terminal


def test_observation_service_has_langfuse_and_signoz_hooks():
    observability = read("src/backend/app/observability.py")
    env_template = read(".env.example")

    assert "from langfuse import Langfuse" in observability
    assert "OTLPSpanExporter" in observability
    assert "FastAPIInstrumentor.instrument_app(app)" in observability
    assert "trace_id" in observability
    assert "span_id" in observability
    assert "LANGFUSE_PUBLIC_KEY" in env_template
    assert "LANGFUSE_SECRET_KEY" in env_template
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in env_template
    assert "signoz-otel-collector:4317" in env_template


def test_observation_service_is_wired_into_compose_and_docs():
    compose = read("docker-compose.agentic-ui.yml")
    docs = read("docs/PROJECT_GUIDE.md")
    readme = read("README.md")

    assert "  observation-service:" in compose
    assert '"18000:8000"' in compose
    assert ".:/workspace:ro" in compose
    assert "POST /terminal/run" in docs
    assert "Observation Service Contract" in docs
    assert "Langfuse" in docs
    assert "SigNoz" in docs
    assert "Observation Service | http://localhost:18000" in readme


def test_observation_service_audit_plan_is_documented():
    audit = read("docs/PROJECT_GUIDE.md")

    assert "Single Environment Model" in audit
    assert "What Was Removed" in audit
    assert "git status && whoami" not in read("src/backend/app/terminal.py")
