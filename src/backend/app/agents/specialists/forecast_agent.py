"""Forecast specialist agent."""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...security import Principal
from ...services.llm import LlmService
from ..table_selector import TableSelector
from .specialist_utils import (
    AgentResponse,
    AgentTask,
    EventSink,
    _clickhouse_query,
    _clickhouse_where_clause,
    _extract_entity_filters,
    _filter_warnings,
    _forecast_confidence,
    _forecast_domain_preferred_columns,
    _forecast_expression_for_table,
    _forecast_history,
    _forecast_metric_columns,
    _forecast_metric_plan,
    _llm_usage_from_result,
    _metadata_evidence,
    _parse_days,
    _resolve_specialist_table,
    _run_llm_synthesis,
    _select_forecast_model,
    _slot_value,
    _sql_evidence,
    _table_column_names,
)


class ForecastAgent:
    def __init__(
        self,
        clickhouse: Any,
        settings: Settings | None = None,
        llm: LlmService | None = None,
        *,
        metadata: Any | None = None,
    ) -> None:
        self.clickhouse = clickhouse
        self.settings = settings
        self.llm = llm
        self.metadata = metadata
        self.table_selector = TableSelector(settings) if settings is not None else None

    async def run(
        self,
        task: AgentTask,
        *,
        question: str,
        target: str,
        limit: int,
        conversation: list[dict[str, Any]],
        memory: dict[str, Any],
        principal: Principal | None,
        include_synthesis: bool,
        event_sink: EventSink,
    ) -> AgentResponse:
        del target, conversation, memory, event_sink
        slots = task.context.get("slots") or {}
        days = _parse_days(_slot_value(slots, "time_range"), default=30)
        filters = _extract_entity_filters(question)
        metric_plan = _forecast_metric_plan(
            question,
            slots,
            service_scoped=bool(filters.get("service_name")),
        )

        metric_columns = _forecast_metric_columns(metric_plan)
        requested_filter_columns = {
            column
            for column in ("service_name", "source_app", "model")
            if filters.get(column)
        }
        preferred_columns = set(requested_filter_columns)
        if not filters.get("service_name"):
            preferred_columns.add(metric_plan["targetMetric"])
            preferred_columns.update(_forecast_domain_preferred_columns(metric_plan))
        resolution = await _resolve_specialist_table(
            question,
            clickhouse=self.clickhouse,
            metadata=self.metadata,
            table_selector=self.table_selector,
            required_columns={"metric_date"},
            any_columns=metric_columns,
            preferred_columns=preferred_columns,
        )
        if resolution.table is None or resolution.table_name is None:
            text = (
                "Forecast agent could not find a live time-series table "
                f"with columns for {metric_plan['targetMetric']}."
            )
            return AgentResponse(
                task_id=task.task_id,
                agent="forecast_agent",
                status="partial",
                text=text,
                evidence=[_metadata_evidence(resolution, metric_plan["targetMetric"])],
                artifacts=[
                    {
                        "type": "forecast",
                        "result": {
                            "model": "table_not_found",
                            "targetMetric": metric_plan["targetMetric"],
                            "unit": metric_plan["unit"],
                            "selectedTables": [],
                            "sql": None,
                            "history": [],
                            "forecast": [],
                            "modelCandidates": [],
                            "trendPrediction": {
                                "direction": "unknown",
                                "reason": "No live table with the required metric columns was found.",
                            },
                            "timeRangeDays": days,
                            "appliedFilters": filters,
                            "metadataSource": resolution.metadata_source,
                            "tableSelection": resolution.table_selection,
                            "requiredColumns": resolution.required_columns,
                        },
                    }
                ],
                confidence=0.2,
                warnings=resolution.warnings or [text],
            )
        chosen_table = resolution.table_name
        table_columns = _table_column_names(resolution.table)
        forecast_expression = _forecast_expression_for_table(metric_plan, resolution.table)
        if forecast_expression is None:
            text = (
                "Forecast agent selected a live table, but it does not expose "
                f"a queryable expression for {metric_plan['targetMetric']}."
            )
            return AgentResponse(
                task_id=task.task_id,
                agent="forecast_agent",
                status="partial",
                text=text,
                evidence=[_metadata_evidence(resolution, metric_plan["targetMetric"])],
                artifacts=[
                    {
                        "type": "forecast",
                        "result": {
                            "model": "metric_column_not_found",
                            "targetMetric": metric_plan["targetMetric"],
                            "unit": metric_plan["unit"],
                            "selectedTables": [chosen_table],
                            "sql": None,
                            "history": [],
                            "forecast": [],
                            "modelCandidates": [],
                            "trendPrediction": {
                                "direction": "unknown",
                                "reason": "The selected table is missing a supported metric column.",
                            },
                            "timeRangeDays": days,
                            "appliedFilters": filters,
                            "metadataSource": resolution.metadata_source,
                            "tableSelection": resolution.table_selection,
                            "requiredColumns": resolution.required_columns,
                        },
                    }
                ],
                confidence=0.2,
                warnings=resolution.warnings or [text],
            )
        filter_warnings = _filter_warnings(
            filters,
            {"service_name", "source_app", "model"},
            table_columns,
        )
        where_clause = _clickhouse_where_clause(
            days=days,
            filters=filters,
            allowed_columns={"service_name", "source_app", "model"} & table_columns,
        )
        sql = f"""
            SELECT
                metric_date,
                {forecast_expression} AS forecast_value
            FROM {chosen_table}
            WHERE {where_clause}
            GROUP BY metric_date
            ORDER BY metric_date ASC
            LIMIT 60
        """
        rows, executed_sql = await _clickhouse_query(
            self.clickhouse,
            sql,
            limit=max(limit, 60),
            principal=principal,
        )
        history = _forecast_history(rows, metric_name=metric_plan["targetMetric"])
        if len(history) < 2:
            return AgentResponse(
                task_id=task.task_id,
                agent="forecast_agent",
                status="partial",
                text=(
                    "Forecast agent needs at least two historical "
                    f"{metric_plan['targetMetric']} points to predict the next trend."
                ),
                evidence=[_sql_evidence(executed_sql, len(rows), chosen_table)],
                artifacts=[
                    {
                        "type": "forecast",
                        "result": {
                            "model": "insufficient_history",
                            "targetMetric": metric_plan["targetMetric"],
                            "unit": metric_plan["unit"],
                            "selectedTables": [chosen_table],
                            "sql": executed_sql,
                            "history": history,
                            "forecast": [],
                            "modelCandidates": [],
                            "trendPrediction": {
                                "direction": "unknown",
                                "reason": "At least two historical points are required.",
                            },
                            "timeRangeDays": days,
                            "appliedFilters": filters,
                            "metadataSource": resolution.metadata_source,
                            "tableSelection": resolution.table_selection,
                            "requiredColumns": resolution.required_columns,
                        },
                    }
                ],
                confidence=0.25,
                warnings=[
                    *resolution.warnings,
                    *filter_warnings,
                    "Insufficient historical points for forecasting.",
                ],
            )
        forecast_result = _select_forecast_model(
            history,
            horizon_days=7,
            metric_name=metric_plan["targetMetric"],
            unit=metric_plan["unit"],
        )
        forecast_points = forecast_result["forecast"]
        selected_model = forecast_result["selectedModel"]
        trend_prediction = forecast_result["trendPrediction"]
        unit = metric_plan["unit"]
        text = (
            "Forecast agent evaluated multiple time-series models "
            "(linear regression, recent trend, moving average, EWMA). "
            f"Selected {selected_model}; next trend is "
            f"{trend_prediction['direction']} with expected change "
            f"{trend_prediction['expectedChange']:.2f} {unit} over "
            f"{trend_prediction['horizonDays']} days."
        )
        usage = {}
        warnings = [*resolution.warnings, *filter_warnings]
        if include_synthesis and self.llm:
            llm_result, synthesis_warning = await _run_llm_synthesis(
                self.llm.interpret_forecast(
                    question=question,
                    target_metric=metric_plan["targetMetric"],
                    unit=unit,
                    history=history,
                    forecast=forecast_points,
                    trend_prediction=trend_prediction,
                ),
                settings=self.settings,
                label="Forecast agent",
            )
            if llm_result.get("status") == "success" and llm_result.get("answer"):
                text = llm_result["answer"]
                usage = _llm_usage_from_result(llm_result)
            elif synthesis_warning:
                warnings.append(synthesis_warning)

        return AgentResponse(
            task_id=task.task_id,
            agent="forecast_agent",
            status="success",
            text=text,
            evidence=[
                _sql_evidence(
                    executed_sql,
                    len(rows),
                    chosen_table,
                )
            ],
            artifacts=[
                {
                    "type": "forecast",
                    "result": {
                        "model": selected_model,
                        "targetMetric": metric_plan["targetMetric"],
                        "unit": unit,
                        "selectedTables": [chosen_table],
                        "sql": executed_sql,
                        "history": history,
                        "forecast": forecast_points,
                        "parameters": forecast_result["parameters"],
                        "modelCandidates": forecast_result["modelCandidates"],
                        "trendPrediction": trend_prediction,
                        "timeRangeDays": days,
                        "appliedFilters": filters,
                        "metadataSource": resolution.metadata_source,
                        "tableSelection": resolution.table_selection,
                        "requiredColumns": resolution.required_columns,
                    },
                }
            ],
            confidence=_forecast_confidence(forecast_result, len(history)),
            warnings=warnings,
            usage=usage,
        )
