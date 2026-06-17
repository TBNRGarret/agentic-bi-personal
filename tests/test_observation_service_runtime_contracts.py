import shlex
import sys
from pathlib import Path

import pytest

from src.backend.app.sql_guard import SqlRejected, validate_readonly_sql
from src.backend.app.terminal import TerminalCommandRejected, TerminalRunner


ROOT = Path(__file__).resolve().parents[1]


def runner(*allowlist: tuple[str, ...], timeout_seconds: int = 2, max_output_chars: int = 12000) -> TerminalRunner:
    return TerminalRunner(
        repo_root=ROOT,
        allowlist=allowlist,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )


def test_terminal_runner_executes_allowlisted_command_without_shell():
    result = runner(("pwd",)).run("pwd")

    assert result.exit_code == 0
    assert result.stdout.strip() == str(ROOT)
    assert result.argv == ["pwd"]


@pytest.mark.parametrize(
    "command",
    (
        "sh -c pwd",
        "rm -rf /tmp/example",
        "cat .env",
        "git status && whoami",
    ),
)
def test_terminal_runner_rejects_non_allowlisted_or_shell_control_commands(command: str):
    with pytest.raises(TerminalCommandRejected):
        runner(("git", "status"), ("pwd",)).run(command)


def test_terminal_runner_rejects_cwd_escape():
    with pytest.raises(TerminalCommandRejected):
        runner(("pwd",)).run("pwd", cwd="..")


def test_terminal_runner_returns_timeout_result():
    command = f"{shlex.quote(sys.executable)} -c \"__import__('time').sleep(2)\""
    result = runner((sys.executable, "-c")).run(command, timeout_seconds=0.1)

    assert result.exit_code == 124
    assert result.timed_out is True


def test_terminal_runner_truncates_output():
    command = f"{shlex.quote(sys.executable)} -c \"print('abcdefghijk')\""
    result = runner((sys.executable, "-c"), max_output_chars=5).run(command)

    assert result.stdout == "abcde\n[output truncated]"


@pytest.mark.parametrize("sql", ("SELECT 1", "show tables", "DESCRIBE vsf_mart.gold_app_daily_cost"))
def test_validate_readonly_sql_accepts_read_only_statements(sql: str):
    validate_readonly_sql(sql)


@pytest.mark.parametrize("sql", ("DROP TABLE x", "INSERT INTO x VALUES (1)", "SELECT 1; SELECT 2"))
def test_validate_readonly_sql_rejects_writes_and_multi_statement(sql: str):
    with pytest.raises(SqlRejected):
        validate_readonly_sql(sql)
