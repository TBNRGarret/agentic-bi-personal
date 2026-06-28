from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .execution_graph import ExecutionGraph, ExecutionStep, topological_order
from .result_composer import compose_step_results

StepHandler = Callable[[ExecutionStep, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class StepExecutionState:
    step_id: str
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stepId": self.step_id,
            "status": self.status,
            "output": self.output,
            "error": self.error,
        }


@dataclass(frozen=True)
class GraphExecutionResult:
    status: str
    order: list[str]
    steps: list[StepExecutionState]
    composed: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "order": self.order,
            "steps": [step.to_dict() for step in self.steps],
            "composed": self.composed,
        }


class DeterministicGraphExecutor:
    """Runs a validated execution graph with explicit, allowlisted step handlers.

    The executor does not let an LLM call tools directly. The LLM may propose
    graph structure, but backend code must provide handlers keyed by step type
    or tool name. This keeps future multi-tool execution deterministic.
    """

    """
    Overview
    1. Receives ExecutionGraph (DAG of steps) + handler map {step_type: async_handler}
    2. Topological sort: determine execution order
    3. Execute step by step
        a. Check dependencies -> skip if failed
        b. Lookup handler by step.type or step.tool
        c. Execute handler(step, context)
        d. Store result in StepExecutionState
    4. After all steps -> compose results via compose_step_results()
    5. Return GraphExecutionResult {status, order, steps, composed}

    Safety: only allowlisted handlers can execute. No LLM tool-calling.
    Designed for future multi-tool execution graphs.
    """

    async def run(
        self,
        graph: ExecutionGraph,
        handlers: dict[str, StepHandler],
        *,
        initial_context: dict[str, Any] | None = None,
    ) -> GraphExecutionResult:
        steps_by_id = {step.id: step for step in graph.steps}
        order = topological_order(graph.steps)
        states: dict[str, StepExecutionState] = {}
        context = dict(initial_context or {})

        for step_id in order:
            step = steps_by_id[step_id]
            failed_dep = next(
                (dep for dep in step.depends_on if states.get(dep) and states[dep].status != "success"),
                None,
            )
            if failed_dep:
                states[step_id] = StepExecutionState(
                    step_id=step_id,
                    status="skipped",
                    error=f"Dependency {failed_dep} did not succeed",
                )
                continue

            handler = handlers.get(step.type) or handlers.get(step.tool)
            if handler is None:
                states[step_id] = StepExecutionState(
                    step_id=step_id,
                    status="planned",
                    output=step.outputs,
                )
                context[step_id] = step.outputs
                continue

            try:
                output = await handler(step, context)
                states[step_id] = StepExecutionState(
                    step_id=step_id,
                    status=str(output.get("status") or "success"),
                    output=output,
                )
                context[step_id] = output
            except Exception as exc:
                states[step_id] = StepExecutionState(
                    step_id=step_id,
                    status="error",
                    error=str(exc),
                )

        ordered_states = [states[step_id] for step_id in order]
        status = "error" if any(step.status == "error" for step in ordered_states) else "success"
        return GraphExecutionResult(
            status=status,
            order=order,
            steps=ordered_states,
            composed=compose_step_results(graph, {key: value.to_dict() for key, value in states.items()}),
        )
