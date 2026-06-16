from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from .config import Settings
from .observability import LangfuseRecorder, current_trace_context, get_tracer
from .terminal import TerminalCommandRejected, TerminalRunner


@dataclass(frozen=True)
class CommandPlan:
    command: str
    reason: str
    confidence: float


class TerminalAgent:
    def __init__(
        self,
        *,
        settings: Settings,
        terminal: TerminalRunner,
        langfuse: LangfuseRecorder,
        logger: logging.Logger,
    ):
        self.settings = settings
        self.terminal = terminal
        self.langfuse = langfuse
        self.logger = logger
        self.tracer = get_tracer("agentic_bi.observation.terminal_agent")

    def answer(self, message: str, user_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        trace_id = uuid.uuid4().hex
        with self.tracer.start_as_current_span("terminal_agent.chat") as span:
            span.set_attribute("app.source_app_id", self.settings.source_app_id)
            span.set_attribute("chat.session_id", session_id or "")
            span.set_attribute("chat.user_id", user_id or "")
            plan = self._plan(message)
            span.set_attribute("terminal.command", plan.command)
            span.set_attribute("terminal.plan_confidence", plan.confidence)
            metadata = {
                "source_app_id": self.settings.source_app_id,
                "environment": self.settings.environment,
                "user_id": user_id,
                "session_id": session_id,
                "otel_trace_id": current_trace_context()[0],
            }

            usage = self._usage(message, plan.command)
            with self.langfuse.generation(
                name="terminal_command_plan",
                trace_id=trace_id,
                input_text=message,
                model="deterministic-terminal-planner",
                metadata=metadata,
            ) as generation:
                try:
                    result = self.terminal.run(plan.command)
                    output_text = self._summarize(plan, result.stdout, result.stderr, result.exit_code)
                    status = "success" if result.exit_code == 0 else "error"
                    generation.end(
                        output=output_text,
                        usage=usage,
                        status=status,
                        status_message=None if status == "success" else result.stderr,
                    )
                    self.langfuse.flush()
                    self.logger.info(
                        "terminal_chat_completed",
                        extra={
                            "extra": {
                                "source_app_id": self.settings.source_app_id,
                                "command": plan.command,
                                "exit_code": result.exit_code,
                                "duration_ms": result.duration_ms,
                            }
                        },
                    )
                    return {
                        "trace_id": trace_id,
                        "message": output_text,
                        "plan": asdict(plan),
                        "terminal": result.to_dict(),
                        "usage": usage,
                    }
                except TerminalCommandRejected as error:
                    span.record_exception(error)
                    output_text = f"Không chạy lệnh vì không nằm trong allowlist: {error}"
                    generation.end(output=output_text, usage=usage, status="error", status_message=str(error))
                    self.langfuse.flush()
                    self.logger.warning(
                        "terminal_chat_rejected",
                        extra={"extra": {"source_app_id": self.settings.source_app_id, "reason": str(error)}},
                    )
                    return {
                        "trace_id": trace_id,
                        "message": output_text,
                        "plan": asdict(plan),
                        "terminal": None,
                        "usage": usage,
                    }

    def _plan(self, message: str) -> CommandPlan:
        text = message.lower()
        if any(token in text for token in ("git status", "trạng thái git", "trang thai git")):
            return CommandPlan(command="git status --short", reason="User asked for repository status.", confidence=0.95)
        if any(token in text for token in ("python", "version", "phiên bản", "phien ban")):
            return CommandPlan(command="python --version", reason="User asked for Python version.", confidence=0.85)
        if any(token in text for token in ("ngày", "ngay", "date", "time", "giờ", "gio")):
            return CommandPlan(command="date", reason="User asked for current date or time.", confidence=0.9)
        if any(token in text for token in ("whoami", "tôi là ai", "toi la ai", "user hiện tại", "user hien tai")):
            return CommandPlan(command="whoami", reason="User asked for current OS user.", confidence=0.9)
        if any(token in text for token in ("pwd", "thư mục", "thu muc", "working directory", "ở đâu", "o dau")):
            return CommandPlan(command="pwd", reason="User asked for the working directory.", confidence=0.85)
        if any(token in text for token in ("ls", "list", "liệt kê", "liet ke", "files", "file")):
            return CommandPlan(command="ls", reason="User asked to list repository files.", confidence=0.8)
        return CommandPlan(command="pwd", reason="Fallback to a harmless repository context command.", confidence=0.25)

    def _usage(self, prompt: str, completion: str) -> dict[str, int]:
        input_tokens = max(1, len(prompt.split()))
        output_tokens = max(1, len(completion.split()))
        return {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        }

    def _summarize(self, plan: CommandPlan, stdout: str, stderr: str, exit_code: int) -> str:
        if exit_code == 0:
            body = stdout.strip() or "(lệnh chạy thành công nhưng không có stdout)"
            return f"Đã chạy `{plan.command}`. Kết quả:\n{body}"
        body = stderr.strip() or stdout.strip() or "(không có output lỗi)"
        return f"Lệnh `{plan.command}` kết thúc với exit code {exit_code}:\n{body}"
