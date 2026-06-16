from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_observation_service_exposes_plan_endpoints():
    main = read("src/backend/app/main.py")

    for route in ('@app.post("/chat")', '@app.post("/terminal/run")', '@app.post("/query")', '@app.get("/schema")'):
        assert route in main


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
    compose = read("docker-compose.override.yml")
    env_template = read(".env.observation.example")

    assert "from langfuse import Langfuse" in observability
    assert "OTLPSpanExporter" in observability
    assert "FastAPIInstrumentor.instrument_app(app)" in observability
    assert "trace_id" in observability
    assert "span_id" in observability
    assert "LANGFUSE_PUBLIC_KEY" in env_template
    assert "LANGFUSE_SECRET_KEY" in env_template
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in env_template
    assert "signoz-otel-collector:4317" in compose


def test_observation_service_is_wired_into_compose_and_docs():
    compose = read("docker-compose.override.yml")
    docs = read("docs/OBSERVATION_SERVICE.md")
    readme = read("README.md")

    assert "  observation-service:" in compose
    assert '"18000:8000"' in compose
    assert ".:/workspace:ro" in compose
    assert "POST /terminal/run" in docs
    assert "Langfuse" in docs
    assert "SigNoz" in docs
    assert "Observation Service | http://localhost:18000" in readme


def test_observation_service_audit_plan_is_documented():
    audit = read("docs/OBSERVATION_SERVICE_AUDIT.md")

    assert "Security Checks" in audit
    assert "Telemetry Checks" in audit
    assert "Audit Prompt" in audit
    assert "git status && whoami" not in read("src/backend/app/terminal.py")
