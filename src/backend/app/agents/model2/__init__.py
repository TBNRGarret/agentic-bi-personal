"""Model 2 hybrid Text2SQL pipeline.

Model 2 combines MAC-SQL's selector/decomposer/refiner loop with
QueryWeaver-style schema retrieval and Agentic BI's governed runtime.
"""

from typing import Any

__all__ = ["HybridText2SQLOrchestrator"]


def __getattr__(name: str) -> Any:
    if name == "HybridText2SQLOrchestrator":
        from .orchestrator import HybridText2SQLOrchestrator

        return HybridText2SQLOrchestrator
    raise AttributeError(name)
