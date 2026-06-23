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

PROJECT_DIR = "/home/tuanvan03/Documents/VINAI/VSF_TTS/agentic-bi2/agentic-bi"
BRANCH = "dev-agent"

COMPOSE_FILES = [
    "docker-compose.yml",
    "docker-compose.agentic-ui.yml",
    "docker-compose.override.yml",
]


def _compose_cmd(c: Connection) -> str:
    """Build the docker compose command with all compose files."""
    files = " ".join(f"-f {f}" for f in COMPOSE_FILES)
    return f"docker compose {files}"


@task
def push(c):
    """Commit and push local changes to dev-agent branch."""
    local_run("git add -A")
    result = local_run("git diff --cached --quiet", warn=True)
    if result.failed:
        msg = f"chore: update before deploy [$(date +%Y-%m-%d_%H:%M)]"
        local_run(f'git commit -m "{msg}"')
    else:
        print("[SKIP] No changes to commit.")
    local_run(f"git push origin {BRANCH}")


@task
def pull(c):
    """SSH into VPS and git pull latest dev-agent branch."""
    with c.cd(PROJECT_DIR):
        c.run(f"git pull origin {BRANCH}", echo=True)


@task
def build(c):
    """Rebuild Docker images on VPS."""
    with c.cd(PROJECT_DIR):
        cmd = _compose_cmd(c)
        c.run(f"{cmd} build", echo=True, pty=True)


@task
def up(c):
    """Start / recreate all Docker services on VPS."""
    with c.cd(PROJECT_DIR):
        cmd = _compose_cmd(c)
        c.run(f"{cmd} up -d", echo=True, pty=True)


@task
def restart(c):
    """Restart all Docker services on VPS."""
    with c.cd(PROJECT_DIR):
        cmd = _compose_cmd(c)
        c.run(f"{cmd} restart", echo=True, pty=True)


@task
def down(c):
    """Stop all Docker services on VPS."""
    with c.cd(PROJECT_DIR):
        cmd = _compose_cmd(c)
        c.run(f"{cmd} down", echo=True)


@task
def status(c):
    """Show container status on VPS."""
    with c.cd(PROJECT_DIR):
        cmd = _compose_cmd(c)
        c.run(f"{cmd} ps", echo=True)


@task
def logs(c, service="", lines=50):
    """Tail logs from VPS services.

    Usage:
        fab deploy.logs
        fab deploy.logs --service observation-service
    """
    with c.cd(PROJECT_DIR):
        cmd = _compose_cmd(c)
        tail = f"--tail {lines}" if lines else ""
        svc = service or ""
        c.run(f"{cmd} logs -f {tail} {svc}", echo=True, pty=True)


@task
def deploy(c):
    """Full deploy: push local → pull VPS → build → restart services."""
    local_run(f"git pull --rebase origin {BRANCH}")
    push(c)
    pull(c)
    build(c)
    up(c)
    restart(c)
    status(c)
    print("\nDeploy complete. Run `fab deploy.logs` to monitor.")


ns = Collection(
    push,
    pull,
    build,
    up,
    restart,
    down,
    status,
    logs,
    deploy,
)
