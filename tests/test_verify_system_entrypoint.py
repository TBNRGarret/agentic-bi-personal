import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_verify_system_can_run_as_direct_script():
    result = subprocess.run(
        [sys.executable, "scripts/verify_system.py", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Verify Sprint 1 Agentic BI local acceptance criteria" in result.stdout
