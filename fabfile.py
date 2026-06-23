"""
Fabric deployment file for Agentic BI.

Requirements:
    pip install fabric

Usage:
    fab deploy             # full deploy (push local → pull VPS → build → restart)
    fab deploy.logs        # tail logs

# ── Local SSH config ──────────────────────────────────────────────────────
# Host vps
#     HostName <VPS_IP>
#     User <username>
#     Port 22
"""

from fabric import Connection, task
from invoke import Collection, run as local_run
from invoke import env

env.hosts = ["vps"]

PROJECT_DIR = "/home/ubuntu/agentic-bi"
BRANCH = "dev-agent"

COMPOSE_FILES = [
    "docker-compose.yml",
    "docker-compose.agentic-ui.yml",
]


def _compose_cmd(c: Connection) -> str:
    """Build the docker compose command with all compose files."""
    files = " ".join(f"-f {f}" for f in COMPOSE_FILES)
    return f"docker compose {files}"


def _run(c: Connection, cmd: str, **kwargs):
    """Run command inside PROJECT_DIR (thay thế c.cd bị lỗi)."""
    c.run(f"cd {PROJECT_DIR} && {cmd}", **kwargs)


@task
def push(c):
    """Push local commits to dev-agent branch."""
    local_run(f"git push origin {BRANCH}")


@task
def build(c):
    """Rebuild Docker images on VPS."""
    _run(c, f"{_compose_cmd(c)} build", echo=True, pty=True)


@task
def up(c):
    """Start / recreate all Docker services on VPS."""
    _run(c, f"{_compose_cmd(c)} up -d", echo=True, pty=True)


@task
def restart(c):
    """Restart all Docker services on VPS."""
    _run(c, f"{_compose_cmd(c)} restart", echo=True, pty=True)


@task
def down(c):
    """Stop all Docker services on VPS."""
    _run(c, f"{_compose_cmd(c)} down", echo=True)


@task
def status(c):
    """Show container status on VPS."""
    _run(c, f"{_compose_cmd(c)} ps", echo=True)


@task
def logs(c, service="", lines=50):
    """Tail logs from VPS services.

    Usage:
        fab deploy.logs
        fab deploy.logs --service observation-service
    """
    tail = f"--tail {lines}" if lines else ""
    svc = service or ""
    _run(c, f"{_compose_cmd(c)} logs -f {tail} {svc}", echo=True, pty=True)


@task
def deploy(c):
    """Full deploy: sync local → push → pull VPS → build → up (tự restart container mới)."""
    local_run(f"git pull --rebase origin {BRANCH}")
    push(c)
    build(c)
    up(c)
    status(c)
    print("\nDeploy complete. Run `fab deploy.logs` to monitor.")


ns = Collection(
    push,
    build,
    up,
    restart,
    down,
    status,
    logs,
    deploy,
)
