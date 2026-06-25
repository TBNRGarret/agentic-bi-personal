#!/usr/bin/env python3
"""
Golden Set Evaluation Runner for Agentic BI Agent.

Sends each golden-set question to the Observation Service /api/query/chat endpoint,
evaluates the response against expected tables, SQL keywords, and visualization,
and generates a Markdown report.

Usage:
    python tests/run_evaluation.py                          # default API URL
    python tests/run_evaluation.py --api-url http://host:18000
    python tests/run_evaluation.py --ids GS-001 GS-005      # run specific cases
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Force UTF-8 output on Windows to avoid encoding errors with Vietnamese text
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GOLDEN_SET_PATH = Path(__file__).with_name("golden_set.json")
REPORT_PATH = Path(__file__).with_name("evaluation_report.md")

DEFAULT_API_URL = os.getenv(
    "EVAL_API_URL", "http://47.131.228.222:18000"
)
DEFAULT_USERNAME = os.getenv("EVAL_USERNAME", "admin")
DEFAULT_PASSWORD = os.getenv("EVAL_PASSWORD", "AgenticBI_Admin_Local_2026!")

REQUEST_TIMEOUT = float(os.getenv("EVAL_TIMEOUT_SECONDS", "120"))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TestCaseResult:
    test_id: str
    category: str
    difficulty: str
    question: str
    description: str

    # Checks
    table_match: bool = False
    table_match_detail: str = ""
    sql_generated: bool = False
    sql_text: str = ""
    sql_keywords_match: bool = False
    sql_keywords_detail: str = ""
    execution_success: bool = False
    row_count: int = 0
    viz_match: bool = False
    viz_detail: str = ""

    # Metadata
    status: str = "not_run"
    agent_answer: str = ""
    latency_ms: int = 0
    error: str = ""
    raw_response: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def authenticate(client: httpx.Client, api_url: str, username: str, password: str) -> str:
    """Login and return JWT access token."""
    resp = client.post(
        f"{api_url}/api/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("accessToken")
    if not token:
        raise RuntimeError(f"Login succeeded but no accessToken in response: {resp.json()}")
    return token


def send_question(
    client: httpx.Client,
    api_url: str,
    token: str,
    question: str,
    target: str = "auto",
    limit: int = 100,
) -> dict[str, Any]:
    """Send a natural-language question to the agent chat endpoint."""
    resp = client.post(
        f"{api_url}/api/query/chat",
        json={
            "question": question,
            "target": target,
            "limit": limit,
            "includeSynthesis": False,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------


def evaluate_table_match(
    response: dict[str, Any], expected_tables: list[str]
) -> tuple[bool, str]:
    """Check if the agent selected at least one of the expected tables."""
    selected = response.get("selectedTables") or []
    selected_lower = [t.lower() for t in selected]
    matched = [t for t in expected_tables if t.lower() in selected_lower]
    if matched:
        return True, f"Matched: {matched} (selected: {selected})"
    # Also check inside SQL text for table references
    sql = (response.get("sql") or "").lower()
    sql_matched = [t for t in expected_tables if t.lower() in sql]
    if sql_matched:
        return True, f"Found in SQL: {sql_matched} (selected: {selected})"
    return False, f"Expected any of {expected_tables}, got {selected}"


def evaluate_sql_keywords(
    response: dict[str, Any], expected_keywords: list[str]
) -> tuple[bool, str]:
    """Check if the generated SQL contains the expected keywords."""
    sql = (response.get("sql") or "").upper()
    if not sql:
        return False, "No SQL was generated"
    found = []
    missing = []
    for kw in expected_keywords:
        if kw.upper() in sql:
            found.append(kw)
        else:
            missing.append(kw)
    passed = len(missing) == 0
    detail = f"Found: {found}"
    if missing:
        detail += f" | Missing: {missing}"
    return passed, detail


def evaluate_visualization(
    response: dict[str, Any], expected_viz: str
) -> tuple[bool, str]:
    """Check if the inferred chart type matches the expectation."""
    chart_spec = response.get("chartSpec") or {}
    actual_type = (chart_spec.get("type") or "table").lower()
    expected_lower = expected_viz.lower()
    matched = actual_type == expected_lower
    return matched, f"Expected: {expected_lower}, Got: {actual_type}"


def run_single_test(
    client: httpx.Client,
    api_url: str,
    token: str,
    test_case: dict[str, Any],
) -> TestCaseResult:
    """Run a single golden-set test case and return the result."""
    result = TestCaseResult(
        test_id=test_case["id"],
        category=test_case["category"],
        difficulty=test_case["difficulty"],
        question=test_case["question"],
        description=test_case["description"],
    )

    start_time = time.perf_counter()
    try:
        response = send_question(
            client,
            api_url,
            token,
            question=test_case["question"],
            target=test_case.get("target", "auto"),
        )
        elapsed = round((time.perf_counter() - start_time) * 1000)
        result.latency_ms = elapsed
        result.raw_response = response

        # Status
        agent_status = response.get("status", "unknown")
        result.status = agent_status

        # Answer
        result.agent_answer = str(response.get("answer") or "")[:500]

        # SQL
        sql = response.get("sql") or ""
        result.sql_text = sql
        result.sql_generated = bool(sql.strip())

        # Execution
        result.execution_success = agent_status == "success"
        result.row_count = response.get("rowCount", 0)

        # Table match
        result.table_match, result.table_match_detail = evaluate_table_match(
            response, test_case["expected_tables"]
        )

        # SQL keywords
        result.sql_keywords_match, result.sql_keywords_detail = evaluate_sql_keywords(
            response, test_case["expected_sql_keywords"]
        )

        # Visualization
        result.viz_match, result.viz_detail = evaluate_visualization(
            response, test_case["expected_visualization"]
        )

    except httpx.HTTPStatusError as exc:
        result.status = "http_error"
        result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        result.latency_ms = round((time.perf_counter() - start_time) * 1000)
    except httpx.TimeoutException:
        result.status = "timeout"
        result.error = f"Request timed out after {REQUEST_TIMEOUT}s"
        result.latency_ms = round((time.perf_counter() - start_time) * 1000)
    except Exception as exc:
        result.status = "exception"
        result.error = str(exc)[:500]
        result.latency_ms = round((time.perf_counter() - start_time) * 1000)

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _pass_fail(value: bool) -> str:
    return "✅" if value else "❌"


def _difficulty_badge(difficulty: str) -> str:
    return {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(difficulty, "⚪")


def generate_report(results: list[TestCaseResult], elapsed_total_s: float) -> str:
    """Generate a Markdown evaluation report."""
    total = len(results)
    if total == 0:
        return "# Evaluation Report\n\nNo test cases were run.\n"

    # Compute metrics
    table_pass = sum(1 for r in results if r.table_match)
    sql_gen = sum(1 for r in results if r.sql_generated)
    exec_pass = sum(1 for r in results if r.execution_success)
    kw_pass = sum(1 for r in results if r.sql_keywords_match)
    viz_pass = sum(1 for r in results if r.viz_match)
    full_pass = sum(
        1
        for r in results
        if r.table_match and r.execution_success and r.sql_keywords_match
    )
    avg_latency = round(sum(r.latency_ms for r in results) / total)

    # Category breakdown
    categories: dict[str, list[TestCaseResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    # Difficulty breakdown
    difficulties: dict[str, list[TestCaseResult]] = {}
    for r in results:
        difficulties.setdefault(r.difficulty, []).append(r)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append("# 🎯 Agent Golden Set — Evaluation Report\n")
    lines.append(f"**Generated at**: {now}  ")
    lines.append(f"**Total execution time**: {elapsed_total_s:.1f}s  ")
    lines.append(f"**Test cases**: {total}\n")

    # ── Summary table ──
    lines.append("## 📊 Overall Metrics\n")
    lines.append("| Metric | Score | Rate |")
    lines.append("|:---|:---:|:---:|")
    lines.append(f"| Table Selection Accuracy | {table_pass}/{total} | {table_pass/total*100:.0f}% |")
    lines.append(f"| SQL Generation | {sql_gen}/{total} | {sql_gen/total*100:.0f}% |")
    lines.append(f"| SQL Execution Success | {exec_pass}/{total} | {exec_pass/total*100:.0f}% |")
    lines.append(f"| SQL Keywords Match | {kw_pass}/{total} | {kw_pass/total*100:.0f}% |")
    lines.append(f"| Visualization Match | {viz_pass}/{total} | {viz_pass/total*100:.0f}% |")
    lines.append(f"| **Full Pass** (table + exec + kw) | **{full_pass}/{total}** | **{full_pass/total*100:.0f}%** |")
    lines.append(f"| Average Latency | {avg_latency}ms | — |")
    lines.append("")

    # ── By category ──
    lines.append("## 📂 Results by Category\n")
    lines.append("| Category | Total | Table | Exec | Keywords | Viz | Full Pass |")
    lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
    for cat, cat_results in sorted(categories.items()):
        cat_total = len(cat_results)
        ct = sum(1 for r in cat_results if r.table_match)
        ce = sum(1 for r in cat_results if r.execution_success)
        ck = sum(1 for r in cat_results if r.sql_keywords_match)
        cv = sum(1 for r in cat_results if r.viz_match)
        cf = sum(1 for r in cat_results if r.table_match and r.execution_success and r.sql_keywords_match)
        lines.append(
            f"| {cat} | {cat_total} | {ct}/{cat_total} | {ce}/{cat_total} "
            f"| {ck}/{cat_total} | {cv}/{cat_total} | {cf}/{cat_total} |"
        )
    lines.append("")

    # ── By difficulty ──
    lines.append("## 🏷️ Results by Difficulty\n")
    lines.append("| Difficulty | Total | Full Pass | Rate |")
    lines.append("|:---|:---:|:---:|:---:|")
    for diff in ("easy", "medium", "hard"):
        diff_results = difficulties.get(diff, [])
        dt = len(diff_results)
        if dt == 0:
            continue
        df = sum(1 for r in diff_results if r.table_match and r.execution_success and r.sql_keywords_match)
        lines.append(
            f"| {_difficulty_badge(diff)} {diff} | {dt} | {df}/{dt} | {df/dt*100:.0f}% |"
        )
    lines.append("")

    # ── Detailed results ──
    lines.append("## 📋 Detailed Results\n")
    lines.append(
        "| # | ID | Difficulty | Question | Table | Exec | KW | Viz | Latency | Status |"
    )
    lines.append("|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
    for i, r in enumerate(results, 1):
        q = r.question[:50] + ("…" if len(r.question) > 50 else "")
        lines.append(
            f"| {i} | {r.test_id} | {_difficulty_badge(r.difficulty)} "
            f"| {q} "
            f"| {_pass_fail(r.table_match)} "
            f"| {_pass_fail(r.execution_success)} "
            f"| {_pass_fail(r.sql_keywords_match)} "
            f"| {_pass_fail(r.viz_match)} "
            f"| {r.latency_ms}ms "
            f"| {r.status} |"
        )
    lines.append("")

    # ── Failed cases detail ──
    failed = [
        r
        for r in results
        if not (r.table_match and r.execution_success and r.sql_keywords_match)
    ]
    if failed:
        lines.append("## ❌ Failed Cases Detail\n")
        for r in failed:
            lines.append(f"### {r.test_id} — {r.question}\n")
            lines.append(f"- **Category**: {r.category} | **Difficulty**: {r.difficulty}")
            lines.append(f"- **Status**: {r.status}")
            if r.error:
                lines.append(f"- **Error**: `{r.error[:300]}`")
            lines.append(f"- **Table Match**: {_pass_fail(r.table_match)} {r.table_match_detail}")
            lines.append(f"- **SQL Keywords**: {_pass_fail(r.sql_keywords_match)} {r.sql_keywords_detail}")
            lines.append(f"- **Visualization**: {_pass_fail(r.viz_match)} {r.viz_detail}")
            lines.append(f"- **Row Count**: {r.row_count}")
            if r.sql_text:
                lines.append(f"- **Generated SQL**:")
                lines.append(f"  ```sql\n  {r.sql_text}\n  ```")
            lines.append("")

    # ── Passed cases SQL ──
    passed_cases = [
        r
        for r in results
        if r.table_match and r.execution_success and r.sql_keywords_match
    ]
    if passed_cases:
        lines.append("## ✅ Passed Cases — Generated SQL\n")
        for r in passed_cases:
            lines.append(f"<details><summary>{r.test_id} — {r.question[:60]}</summary>\n")
            lines.append(f"```sql\n{r.sql_text}\n```")
            lines.append(f"- Rows: {r.row_count} | Latency: {r.latency_ms}ms | Viz: {r.viz_detail}")
            lines.append(f"</details>\n")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Agent Golden Set evaluation")
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"Observation Service base URL (default: {DEFAULT_API_URL})",
    )
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=GOLDEN_SET_PATH,
        help="Path to golden_set.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPORT_PATH,
        help="Path for the Markdown report output",
    )
    parser.add_argument(
        "--ids",
        nargs="*",
        help="Run only specific test case IDs (e.g. GS-001 GS-005)",
    )
    parser.add_argument(
        "--category",
        help="Run only test cases in this category",
    )
    parser.add_argument(
        "--difficulty",
        choices=["easy", "medium", "hard"],
        help="Run only test cases at this difficulty level",
    )
    args = parser.parse_args()

    # Load golden set
    golden_set_path: Path = args.golden_set
    if not golden_set_path.exists():
        print(f"[ERROR] Golden set not found: {golden_set_path}", file=sys.stderr)
        return 1
    test_cases: list[dict[str, Any]] = json.loads(
        golden_set_path.read_text(encoding="utf-8")
    )
    print(f"[INFO] Loaded {len(test_cases)} test cases from {golden_set_path}")

    # Filter if requested
    if args.ids:
        id_set = set(args.ids)
        test_cases = [tc for tc in test_cases if tc["id"] in id_set]
        print(f"[INFO] Filtered to {len(test_cases)} test case(s) by ID")
    if args.category:
        test_cases = [tc for tc in test_cases if tc["category"] == args.category]
        print(f"[INFO] Filtered to {len(test_cases)} test case(s) by category={args.category}")
    if args.difficulty:
        test_cases = [tc for tc in test_cases if tc["difficulty"] == args.difficulty]
        print(f"[INFO] Filtered to {len(test_cases)} test case(s) by difficulty={args.difficulty}")

    if not test_cases:
        print("[WARN] No test cases to run after filtering.", file=sys.stderr)
        return 0

    # Authenticate
    client = httpx.Client()
    try:
        print(f"[INFO] Authenticating to {args.api_url} as {args.username}...")
        token = authenticate(client, args.api_url, args.username, args.password)
        print("[INFO] Authentication successful")
    except Exception as exc:
        print(f"[ERROR] Authentication failed: {exc}", file=sys.stderr)
        return 1

    # Run evaluations
    results: list[TestCaseResult] = []
    total_start = time.perf_counter()
    for i, tc in enumerate(test_cases, 1):
        test_id = tc["id"]
        print(
            f"[{i}/{len(test_cases)}] Running {test_id}: "
            f"{tc['question'][:60]}...",
            end="",
            flush=True,
        )
        result = run_single_test(client, args.api_url, token, tc)
        full_pass = result.table_match and result.execution_success and result.sql_keywords_match
        status_icon = "✅" if full_pass else "❌"
        print(
            f" {status_icon}  "
            f"table={_pass_fail(result.table_match)} "
            f"exec={_pass_fail(result.execution_success)} "
            f"kw={_pass_fail(result.sql_keywords_match)} "
            f"viz={_pass_fail(result.viz_match)} "
            f"({result.latency_ms}ms)"
        )
        results.append(result)

    elapsed_total = time.perf_counter() - total_start
    client.close()

    # Generate report
    report = generate_report(results, elapsed_total)
    report_path: Path = args.report
    report_path.write_text(report, encoding="utf-8")
    print(f"\n[INFO] Evaluation report written to {report_path}")

    # Print summary
    total = len(results)
    full_pass_count = sum(
        1
        for r in results
        if r.table_match and r.execution_success and r.sql_keywords_match
    )
    print(f"[INFO] Overall: {full_pass_count}/{total} full pass ({full_pass_count/total*100:.0f}%)")

    return 0 if full_pass_count == total else 0  # always exit 0, report has details


if __name__ == "__main__":
    sys.exit(main())
