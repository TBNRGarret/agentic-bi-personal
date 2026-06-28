#!/usr/bin/env python3
"""Compare Agentic BI Model 1 and Model 2 on the golden set.

Usage:
    python tests/run_model_comparison.py
    python tests/run_model_comparison.py --api-url http://localhost:18000 --ids GS-001
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from run_evaluation import (
    DEFAULT_API_URL,
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    GOLDEN_SET_PATH,
    REQUEST_TIMEOUT,
    TestCaseResult,
    authenticate,
    evaluate_sql_keywords,
    evaluate_table_match,
    evaluate_visualization,
)

REPORT_PATH = Path(__file__).with_name("model_comparison_report.md")
MODELS = ("model1", "model2")


@dataclass
class ComparisonResult:
    test_id: str
    category: str
    difficulty: str
    question: str
    results: dict[str, TestCaseResult]


def send_question(
    client: httpx.Client,
    api_url: str,
    token: str,
    question: str,
    *,
    model: str,
    target: str = "auto",
    limit: int = 100,
) -> dict[str, Any]:
    response = client.post(
        f"{api_url}/api/query/chat",
        json={
            "question": question,
            "target": target,
            "model": model,
            "limit": limit,
            "includeSynthesis": False,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def run_single_model(
    client: httpx.Client,
    api_url: str,
    token: str,
    test_case: dict[str, Any],
    model: str,
) -> TestCaseResult:
    result = TestCaseResult(
        test_id=test_case["id"],
        category=test_case["category"],
        difficulty=test_case["difficulty"],
        question=test_case["question"],
        description=test_case["description"],
    )
    started = time.perf_counter()
    try:
        response = send_question(
            client,
            api_url,
            token,
            test_case["question"],
            model=model,
            target=test_case.get("target", "auto"),
        )
        result.latency_ms = round((time.perf_counter() - started) * 1000)
        result.raw_response = response
        result.status = response.get("status", "unknown")
        result.agent_answer = str(response.get("answer") or "")[:500]
        result.sql_text = response.get("sql") or ""
        result.sql_generated = bool(result.sql_text.strip())
        result.execution_success = result.status == "success"
        result.row_count = int(response.get("rowCount") or 0)
        result.table_match, result.table_match_detail = evaluate_table_match(
            response, test_case["expected_tables"]
        )
        result.sql_keywords_match, result.sql_keywords_detail = evaluate_sql_keywords(
            response, test_case["expected_sql_keywords"]
        )
        result.viz_match, result.viz_detail = evaluate_visualization(
            response, test_case["expected_visualization"]
        )
    except httpx.HTTPStatusError as exc:
        result.status = "http_error"
        result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        result.latency_ms = round((time.perf_counter() - started) * 1000)
    except httpx.TimeoutException:
        result.status = "timeout"
        result.error = f"Request timed out after {REQUEST_TIMEOUT}s"
        result.latency_ms = round((time.perf_counter() - started) * 1000)
    except Exception as exc:
        result.status = "exception"
        result.error = str(exc)[:500]
        result.latency_ms = round((time.perf_counter() - started) * 1000)
    return result


def full_pass(result: TestCaseResult) -> bool:
    return result.table_match and result.execution_success and result.sql_keywords_match


def mark(value: bool) -> str:
    return "PASS" if value else "FAIL"


def generate_report(comparisons: list[ComparisonResult], elapsed_total_s: float) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Agentic BI Model Comparison Report",
        "",
        f"Generated at: {now}",
        f"Total execution time: {elapsed_total_s:.1f}s",
        f"Test cases: {len(comparisons)}",
        "",
        "## Summary",
        "",
        "| Model | Full Pass | Exec Success | Avg Latency |",
        "|:---|:---:|:---:|---:|",
    ]
    for model in MODELS:
        results = [comparison.results[model] for comparison in comparisons]
        total = len(results) or 1
        pass_count = sum(1 for result in results if full_pass(result))
        exec_count = sum(1 for result in results if result.execution_success)
        avg_latency = round(sum(result.latency_ms for result in results) / total)
        lines.append(
            f"| {model} | {pass_count}/{total} | {exec_count}/{total} | {avg_latency}ms |"
        )
    lines.extend([
        "",
        "## Case Comparison",
        "",
        "| ID | Difficulty | Model 1 | Model 2 | Better Latency | Notes |",
        "|:---|:---:|:---:|:---:|---:|:---|",
    ])
    for comparison in comparisons:
        m1 = comparison.results["model1"]
        m2 = comparison.results["model2"]
        if m1.latency_ms and m2.latency_ms:
            latency_note = "model1" if m1.latency_ms <= m2.latency_ms else "model2"
        else:
            latency_note = "n/a"
        notes = []
        if full_pass(m2) and not full_pass(m1):
            notes.append("model2 improved full pass")
        if full_pass(m1) and not full_pass(m2):
            notes.append("model2 regression")
        if m1.sql_text and m2.sql_text and m1.sql_text != m2.sql_text:
            notes.append("different SQL")
        lines.append(
            f"| {comparison.test_id} | {comparison.difficulty} | {mark(full_pass(m1))} "
            f"| {mark(full_pass(m2))} | {latency_note} | {', '.join(notes) or '-'} |"
        )
    lines.extend(["", "## SQL Details", ""])
    for comparison in comparisons:
        lines.append(f"### {comparison.test_id} - {comparison.question}")
        for model in MODELS:
            result = comparison.results[model]
            lines.append(
                f"- {model}: status={result.status}, rows={result.row_count}, "
                f"latency={result.latency_ms}ms, full_pass={mark(full_pass(result))}"
            )
            if result.error:
                lines.append(f"  - error: `{result.error}`")
            if result.sql_text:
                lines.append(f"```sql\n{result.sql_text}\n```")
        lines.append("")
    return "\n".join(lines)


def filtered_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = json.loads(Path(args.golden_set).read_text(encoding="utf-8"))
    if args.ids:
        wanted = set(args.ids)
        cases = [case for case in cases if case["id"] in wanted]
    if args.category:
        cases = [case for case in cases if case["category"] == args.category]
    if args.difficulty:
        cases = [case for case in cases if case["difficulty"] == args.difficulty]
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Agentic BI Model 1 and Model 2")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--golden-set", type=Path, default=GOLDEN_SET_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--category")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    args = parser.parse_args()

    cases = filtered_cases(args)
    if not cases:
        print("No test cases selected.", file=sys.stderr)
        return 0

    client = httpx.Client()
    try:
        token = authenticate(client, args.api_url, args.username, args.password)
    except Exception as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1

    comparisons: list[ComparisonResult] = []
    started = time.perf_counter()
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['id']} {case['question'][:60]}...")
        results = {
            model: run_single_model(client, args.api_url, token, case, model)
            for model in MODELS
        }
        comparisons.append(
            ComparisonResult(
                test_id=case["id"],
                category=case["category"],
                difficulty=case["difficulty"],
                question=case["question"],
                results=results,
            )
        )
        print(
            "  model1="
            f"{mark(full_pass(results['model1']))} "
            "model2="
            f"{mark(full_pass(results['model2']))}"
        )
    elapsed = time.perf_counter() - started
    client.close()

    report = generate_report(comparisons, elapsed)
    Path(args.report).write_text(report, encoding="utf-8")
    print(f"Report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
