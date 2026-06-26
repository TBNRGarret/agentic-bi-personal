"""
Fabric deployment file for Agentic BI.

Requirements:
    pip install fabric

Usage:
    fab deploy             # full deploy
    fab deploy.logs        # tail logs

# ── Local SSH config ──────────────────────────────────────────────────────
# Host vps
#     HostName <VPS_IP>
#     User <username>
#     Port 22
"""

from fabric import Connection, task
from invoke import Collection, run as local_run
PROJECT_DIR = "agentic-bi"
BRANCH = "dev"
COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.agentic-ui.yml"


@task
def deploy(_):
    """Full deploy: sync local → 1 SSH session: pull + build + up + status."""
    local_run(f"git pull --rebase origin {BRANCH}")
    local_run(f"git push origin {BRANCH}")

    conn = Connection("vps")
    script = f"""
echo "=== Connected to $(hostname) ==="
cd {PROJECT_DIR} || {{ echo "FOLDER NOT FOUND: {PROJECT_DIR}"; exit 1; }}
git pull origin {BRANCH}
{COMPOSE} build
{COMPOSE} up -d
{COMPOSE} ps
"""
    conn.run(script, echo=True, pty=True)
    print("\nDeploy complete.")


@task
def logs(_, service="", lines=50):
    """Tail logs from VPS services.

    Usage:
        fab deploy.logs
        fab deploy.logs --service observation-service
    """
    conn = Connection("vps")
    tail = f"--tail {lines}" if lines else ""
    svc = service or ""
    conn.run(f"cd {PROJECT_DIR} && {COMPOSE} logs -f {tail} {svc}", echo=True, pty=True)


ns = Collection(deploy, logs)
