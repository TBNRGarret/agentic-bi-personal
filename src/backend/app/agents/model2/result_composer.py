from __future__ import annotations

from typing import Any

from .execution_graph import ExecutionGraph


def compose_step_results(
    graph: ExecutionGraph,
    step_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Summarize dependency-graph execution outputs for answer synthesis/debugging."""

    successful = [step_id for step_id, result in step_results.items() if result.get("status") == "success"]
    failed = [step_id for step_id, result in step_results.items() if result.get("status") == "error"]
    skipped = [step_id for step_id, result in step_results.items() if result.get("status") == "skipped"]
    terminal_steps = _terminal_steps(graph)
    terminal_outputs = {
        step_id: step_results.get(step_id, {}).get("output")
        for step_id in terminal_steps
        if step_id in step_results
    }
    return {
        "graphId": graph.graph_id,
        "status": "error" if failed else "success",
        "successfulSteps": successful,
        "failedSteps": failed,
        "skippedSteps": skipped,
        "terminalSteps": terminal_steps,
        "terminalOutputs": terminal_outputs,
    }


def _terminal_steps(graph: ExecutionGraph) -> list[str]:
    dependencies = {dep for step in graph.steps for dep in step.depends_on}
    return [step.id for step in graph.steps if step.id not in dependencies]
