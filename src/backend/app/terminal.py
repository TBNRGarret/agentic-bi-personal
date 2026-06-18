from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


class TerminalCommandRejected(ValueError):
    pass


SHELL_CONTROL_CHARS = (";", "|", "&", ">", "<", "`")


@dataclass(frozen=True)
class TerminalResult:
    command: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class TerminalRunner:
    def __init__(
        self,
        *,
        repo_root: Path,
        allowlist: tuple[tuple[str, ...], ...],
        timeout_seconds: int,
        max_output_chars: int,
    ):
        self.repo_root = repo_root
        self.allowlist = allowlist
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    def run(self, command: str, cwd: str | None = None, timeout_seconds: int | None = None) -> TerminalResult:
        argv = shlex.split(command)
        if not argv:
            raise TerminalCommandRejected("Command is empty.")
        if self._has_shell_control(argv):
            raise TerminalCommandRejected(f"Shell control syntax is not allowed: {command}")
        if not self._is_allowed(argv):
            raise TerminalCommandRejected(f"Command is not allowlisted: {command}")

        workdir = self._resolve_cwd(cwd)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds or self.timeout_seconds,
                check=False,
                shell=False,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            return TerminalResult(
                command=command,
                argv=argv,
                exit_code=completed.returncode,
                stdout=self._truncate(completed.stdout),
                stderr=self._truncate(completed.stderr),
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            return TerminalResult(
                command=command,
                argv=argv,
                exit_code=124,
                stdout=self._truncate(error.stdout or ""),
                stderr=self._truncate(error.stderr or "Command timed out."),
                duration_ms=duration_ms,
                timed_out=True,
            )

    def _is_allowed(self, argv: list[str]) -> bool:
        for allowed in self.allowlist:
            if tuple(argv[: len(allowed)]) == allowed:
                return True
        return False

    def _has_shell_control(self, argv: list[str]) -> bool:
        for token in argv:
            if "$(" in token:
                return True
            if any(char in token for char in SHELL_CONTROL_CHARS):
                return True
        return False

    def _resolve_cwd(self, cwd: str | None) -> Path:
        if cwd is None or cwd.strip() == "":
            return self.repo_root
        requested = (self.repo_root / cwd).resolve()
        if requested != self.repo_root and self.repo_root not in requested.parents:
            raise TerminalCommandRejected("Working directory must stay inside the repository.")
        if not requested.exists() or not requested.is_dir():
            raise TerminalCommandRejected(f"Working directory does not exist: {cwd}")
        return requested

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_output_chars:
            return value
        return value[: self.max_output_chars] + "\n[output truncated]"
