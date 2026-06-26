# 🎯 Agent Golden Set — Evaluation Report

**Generated at**: 2026-06-26 09:11 UTC  
**Total execution time**: 992.6s  
**Test cases**: 30

## 📊 Overall Metrics

| Metric | Score | Rate |
|:---|:---:|:---:|
| Table Selection Accuracy | 4/30 | 13% |
| SQL Generation | 24/30 | 80% |
| SQL Execution Success | 24/30 | 80% |
| SQL Keywords Match | 11/30 | 37% |
| Visualization Match | 16/30 | 53% |
| **Full Pass** (table + exec + kw) | **4/30** | **13%** |
| Average Latency | 33085ms | — |

## 📂 Results by Category

| Category | Total | Table | Exec | Keywords | Viz | Full Pass |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| app_management | 2 | 0/2 | 0/2 | 0/2 | 1/2 | 0/2 |
| cost_analysis | 4 | 1/4 | 4/4 | 2/4 | 3/4 | 1/4 |
| cross_analysis | 2 | 0/2 | 2/2 | 1/2 | 1/2 | 0/2 |
| data_quality | 2 | 1/2 | 1/2 | 1/2 | 1/2 | 1/2 |
| error_analysis | 3 | 0/3 | 3/3 | 0/3 | 2/3 | 0/3 |
| ingestion | 2 | 0/2 | 1/2 | 0/2 | 1/2 | 0/2 |
| interaction_detail | 3 | 0/3 | 2/3 | 0/3 | 2/3 | 0/3 |
| log_analysis | 2 | 0/2 | 2/2 | 1/2 | 2/2 | 0/2 |
| model_comparison | 2 | 1/2 | 2/2 | 1/2 | 0/2 | 1/2 |
| performance | 3 | 0/3 | 3/3 | 3/3 | 1/3 | 0/3 |
| time_analysis | 1 | 0/1 | 1/1 | 0/1 | 0/1 | 0/1 |
| usage | 4 | 1/4 | 3/4 | 2/4 | 2/4 | 1/4 |

## 🏷️ Results by Difficulty

| Difficulty | Total | Full Pass | Rate |
|:---|:---:|:---:|:---:|
| 🟢 easy | 8 | 1/8 | 12% |
| 🟡 medium | 14 | 2/14 | 14% |
| 🔴 hard | 8 | 1/8 | 12% |

## 📋 Detailed Results

| # | ID | Difficulty | Question | Table | Exec | KW | Viz | Latency | Status |
|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | GS-001 | 🟢 | Tổng chi phí LLM của tất cả ứng dụng là bao nhiêu? | ✅ | ✅ | ✅ | ✅ | 31067ms | success |
| 2 | GS-002 | 🟡 | So sánh chi phí LLM theo từng provider | ❌ | ✅ | ❌ | ❌ | 20596ms | success |
| 3 | GS-003 | 🟡 | Chi phí LLM theo ngày trong tuần qua | ❌ | ✅ | ✅ | ✅ | 49095ms | success |
| 4 | GS-004 | 🔴 | Top 3 model tốn chi phí nhiều nhất theo từng sourc… | ❌ | ✅ | ❌ | ✅ | 50349ms | success |
| 5 | GS-005 | 🟢 | Độ trễ trung bình của các request LLM là bao nhiêu… | ❌ | ✅ | ✅ | ✅ | 30485ms | success |
| 6 | GS-006 | 🟡 | So sánh p95 latency giữa các model | ❌ | ✅ | ✅ | ❌ | 55254ms | success |
| 7 | GS-007 | 🟡 | Xu hướng latency trung bình theo ngày | ❌ | ✅ | ✅ | ❌ | 24431ms | success |
| 8 | GS-008 | 🟢 | Tỷ lệ lỗi tổng thể của hệ thống là bao nhiêu? | ❌ | ✅ | ❌ | ✅ | 19360ms | success |
| 9 | GS-009 | 🟡 | Tỷ lệ lỗi theo từng model LLM | ❌ | ✅ | ❌ | ❌ | 39089ms | success |
| 10 | GS-010 | 🔴 | Liệt kê các interaction bị lỗi gần đây kèm chi tiế… | ❌ | ✅ | ❌ | ✅ | 9806ms | success |
| 11 | GS-011 | 🟢 | Tổng số request LLM trong hệ thống là bao nhiêu? | ❌ | ✅ | ❌ | ✅ | 33965ms | success |
| 12 | GS-012 | 🟡 | Số lượng request theo từng source app | ❌ | ❌ | ❌ | ❌ | 14339ms | needs_clarification |
| 13 | GS-013 | 🟡 | Xu hướng số lượng token sử dụng theo ngày | ❌ | ✅ | ✅ | ❌ | 41115ms | success |
| 14 | GS-014 | 🔴 | Phân tích tỷ lệ phân bổ request giữa các provider … | ✅ | ✅ | ✅ | ✅ | 84473ms | success |
| 15 | GS-015 | 🟡 | So sánh hiệu suất giữa gpt-4o và gemini-2.0-flash … | ✅ | ✅ | ✅ | ❌ | 67697ms | success |
| 16 | GS-016 | 🔴 | Model nào có chi phí trung bình trên mỗi request t… | ❌ | ✅ | ❌ | ❌ | 74906ms | success |
| 17 | GS-017 | 🟢 | Liệt kê 10 interaction gần nhất | ❌ | ❌ | ❌ | ✅ | 3697ms | needs_clarification |
| 18 | GS-018 | 🟡 | Những interaction nào tiêu tốn nhiều token nhất? | ❌ | ✅ | ❌ | ✅ | 24866ms | success |
| 19 | GS-019 | 🔴 | Phân bổ latency của các interaction theo khoảng th… | ❌ | ✅ | ❌ | ❌ | 18877ms | success |
| 20 | GS-020 | 🟢 | Danh sách tất cả source app trong hệ thống | ❌ | ❌ | ❌ | ✅ | 10992ms | needs_clarification |
| 21 | GS-021 | 🟡 | Có bao nhiêu app đang hoạt động theo từng domain? | ❌ | ❌ | ❌ | ❌ | 1632ms | needs_clarification |
| 22 | GS-022 | 🟢 | Kết quả kiểm tra chất lượng dữ liệu gần nhất | ❌ | ❌ | ❌ | ✅ | 1108ms | needs_clarification |
| 23 | GS-023 | 🟡 | Bao nhiêu test data quality đang pass và fail? | ✅ | ✅ | ✅ | ❌ | 42169ms | success |
| 24 | GS-024 | 🟢 | Trạng thái các batch ingestion gần đây | ❌ | ✅ | ❌ | ✅ | 34140ms | success |
| 25 | GS-025 | 🟡 | Tổng số dòng đã nạp thành công theo từng source ap… | ❌ | ❌ | ❌ | ❌ | 21988ms | error |
| 26 | GS-026 | 🔴 | So sánh chi phí và tỷ lệ lỗi theo domain | ❌ | ✅ | ❌ | ❌ | 15410ms | success |
| 27 | GS-027 | 🔴 | Mối quan hệ giữa số token sử dụng và chi phí theo … | ❌ | ✅ | ✅ | ✅ | 56203ms | success |
| 28 | GS-028 | 🟡 | Xu hướng chi phí và số request theo ngày trong 7 n… | ❌ | ✅ | ❌ | ❌ | 68409ms | success |
| 29 | GS-029 | 🟡 | Số lượng log ERROR theo service trong tuần qua | ❌ | ✅ | ❌ | ✅ | 19373ms | success |
| 30 | GS-030 | 🔴 | Liệt kê các log lỗi gần đây kèm trace_id để debug | ❌ | ✅ | ✅ | ✅ | 27665ms | success |

## ❌ Failed Cases Detail

### GS-002 — So sánh chi phí LLM theo từng provider

- **Category**: cost_analysis | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ❌ Found: ['SUM', 'total_cost_usd', 'GROUP BY'] | Missing: ['provider']
- **Visualization**: ❌ Expected: bar, Got: line
- **Row Count**: 13
- **Generated SQL**:
  ```sql
  SELECT metric_date, model, round(sum(total_cost_usd), 6) AS total_cost_usd FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY metric_date, model ORDER BY metric_date, model LIMIT 500
  ```

### GS-003 — Chi phí LLM theo ngày trong tuần qua

- **Category**: cost_analysis | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ✅ Found: ['date', 'total_cost_usd']
- **Visualization**: ✅ Expected: line, Got: line
- **Row Count**: 1
- **Generated SQL**:
  ```sql
  SELECT metric_date AS x, sum(total_cost_usd) AS y
FROM gold.gold_llm_usage_daily
WHERE metric_date >= addDays(today(), -7)
GROUP BY metric_date
ORDER BY metric_date ASC
LIMIT 500
  ```

### GS-004 — Top 3 model tốn chi phí nhiều nhất theo từng source app

- **Category**: cost_analysis | **Difficulty**: hard
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ❌ Found: ['SUM', 'total_cost_usd', 'model', 'GROUP BY', 'ORDER BY'] | Missing: ['source_app_id']
- **Visualization**: ✅ Expected: bar, Got: bar
- **Row Count**: 31
- **Generated SQL**:
  ```sql
  SELECT source_app, model, sum(total_cost_usd) AS total_cost_usd FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY source_app, model ORDER BY total_cost_usd DESC LIMIT 100
  ```

### GS-005 — Độ trễ trung bình của các request LLM là bao nhiêu?

- **Category**: performance | **Difficulty**: easy
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got ['silver.silver_llm_generations']
- **SQL Keywords**: ✅ Found: ['avg_latency_ms']
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 1
- **Generated SQL**:
  ```sql
  SELECT toDate(event_time, 'UTC') AS metric_date, round(avg(latency_ms), 2) AS avg_latency_ms, round(quantile(0.95)(latency_ms), 2) AS p95_latency_ms FROM silver.silver_llm_generations WHERE event_time >= now() - INTERVAL 30 DAY AND latency_ms IS NOT NULL GROUP BY metric_date ORDER BY metric_date LIMIT 100
  ```

### GS-006 — So sánh p95 latency giữa các model

- **Category**: performance | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ✅ Found: ['p95_latency_ms', 'model', 'GROUP BY']
- **Visualization**: ❌ Expected: bar, Got: line
- **Row Count**: 13
- **Generated SQL**:
  ```sql
  SELECT metric_date AS date, model, avg(p95_latency_ms) AS p95_latency_ms
FROM gold.gold_llm_usage_daily
WHERE metric_date >= subtractDays(today(), 30)
GROUP BY metric_date, model
ORDER BY metric_date ASC
LIMIT 100
  ```

### GS-007 — Xu hướng latency trung bình theo ngày

- **Category**: performance | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ✅ Found: ['avg_latency_ms', 'date']
- **Visualization**: ❌ Expected: line, Got: table
- **Row Count**: 1
- **Generated SQL**:
  ```sql
  SELECT metric_date, round(avg(avg_latency_ms), 2) AS avg_latency_ms, round(avg(p95_latency_ms), 2) AS p95_latency_ms FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY metric_date ORDER BY metric_date LIMIT 100
  ```

### GS-008 — Tỷ lệ lỗi tổng thể của hệ thống là bao nhiêu?

- **Category**: error_analysis | **Difficulty**: easy
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got ['gold.gold_system_observability_daily']
- **SQL Keywords**: ❌ Found: ['error_rate'] | Missing: ['error_count']
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 4
- **Generated SQL**:
  ```sql
  SELECT service_name, source_app, span_count, error_span_count, round(error_span_count / greatest(span_count, 1) * 100, 2) AS error_rate_pct, avg_latency_ms, p95_latency_ms FROM ( SELECT service_name, source_app, sum(span_count) AS span_count, sum(error_span_count) AS error_span_count, round(avg(avg_span_duration_ms), 2) AS avg_latency_ms, round(avg(p95_span_duration_ms), 2) AS p95_latency_ms FROM gold.gold_system_observability_daily WHERE metric_date >= today() - 30 GROUP BY service_name, source_app ) ORDER BY error_rate_pct DESC, error_span_count DESC, p95_latency_ms DESC LIMIT 20
  ```

### GS-009 — Tỷ lệ lỗi theo từng model LLM

- **Category**: error_analysis | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ❌ Found: ['GROUP BY'] | Missing: ['error_rate', 'model']
- **Visualization**: ❌ Expected: bar, Got: table
- **Row Count**: 1
- **Generated SQL**:
  ```sql
  SELECT metric_date, sum(error_count) AS error_count, sum(success_count) AS success_count FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY metric_date ORDER BY metric_date LIMIT 100
  ```

### GS-010 — Liệt kê các interaction bị lỗi gần đây kèm chi tiết lỗi

- **Category**: error_analysis | **Difficulty**: hard
- **Status**: success
- **Table Match**: ❌ Expected any of ['fct_llm_interactions'], got ['gold.gold_ingestion_health_daily', 'ops.agent_query_audit']
- **SQL Keywords**: ❌ Found: ['ORDER BY'] | Missing: ['status', 'error_details']
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 4
- **Generated SQL**:
  ```sql
  SELECT source_app, batch_count, failed_batch_count, quarantined_batch_count, loaded_row_count, round(failed_batch_count / greatest(batch_count, 1) * 100, 2) AS failed_batch_rate_pct FROM ( SELECT source_app, sum(batch_count) AS batch_count, sum(failed_batch_count) AS failed_batch_count, sum(quarantined_batch_count) AS quarantined_batch_count, sum(loaded_row_count) AS loaded_row_count FROM gold.gold_ingestion_health_daily WHERE metric_date >= today() - 30 GROUP BY source_app ) ORDER BY failed_batch_count DESC, quarantined_batch_count DESC LIMIT 20
  ```

### GS-011 — Tổng số request LLM trong hệ thống là bao nhiêu?

- **Category**: usage | **Difficulty**: easy
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ❌ Found: ['SUM', 'request_count'] | Missing: ['total_requests']
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 1
- **Generated SQL**:
  ```sql
  SELECT metric_date, sum(generation_count) AS request_count FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY metric_date ORDER BY metric_date LIMIT 100
  ```

### GS-012 — Số lượng request theo từng source app

- **Category**: usage | **Difficulty**: medium
- **Status**: needs_clarification
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got []
- **SQL Keywords**: ❌ No SQL was generated
- **Visualization**: ❌ Expected: bar, Got: table
- **Row Count**: 0

### GS-013 — Xu hướng số lượng token sử dụng theo ngày

- **Category**: usage | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ✅ Found: ['total_tokens', 'date']
- **Visualization**: ❌ Expected: line, Got: table
- **Row Count**: 1
- **Generated SQL**:
  ```sql
  SELECT metric_date, sum(generation_count) AS generation_count, sum(total_tokens) AS total_tokens, round(sum(total_cost_usd), 6) AS total_cost_usd FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY metric_date ORDER BY metric_date LIMIT 100
  ```

### GS-016 — Model nào có chi phí trung bình trên mỗi request thấp nhất?

- **Category**: model_comparison | **Difficulty**: hard
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got ['silver.silver_llm_generations']
- **SQL Keywords**: ❌ Found: ['model', 'cost', 'ORDER BY'] | Missing: ['request']
- **Visualization**: ❌ Expected: bar, Got: line
- **Row Count**: 13
- **Generated SQL**:
  ```sql
  SELECT toDate(event_time, 'UTC') AS metric_date, model, round(sum(cost_usd), 6) AS total_cost_usd FROM silver.silver_llm_generations WHERE event_time >= now() - INTERVAL 30 DAY GROUP BY metric_date, model ORDER BY metric_date, model LIMIT 100
  ```

### GS-017 — Liệt kê 10 interaction gần nhất

- **Category**: interaction_detail | **Difficulty**: easy
- **Status**: needs_clarification
- **Table Match**: ❌ Expected any of ['fct_llm_interactions'], got []
- **SQL Keywords**: ❌ No SQL was generated
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 0

### GS-018 — Những interaction nào tiêu tốn nhiều token nhất?

- **Category**: interaction_detail | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['fct_llm_interactions'], got ['gold.gold_system_observability_daily']
- **SQL Keywords**: ❌ Found: ['ORDER BY', 'DESC'] | Missing: ['total_tokens']
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 4
- **Generated SQL**:
  ```sql
  SELECT service_name, source_app, span_count, error_span_count, round(error_span_count / greatest(span_count, 1) * 100, 2) AS error_rate_pct, avg_latency_ms, p95_latency_ms FROM ( SELECT service_name, source_app, sum(span_count) AS span_count, sum(error_span_count) AS error_span_count, round(avg(avg_span_duration_ms), 2) AS avg_latency_ms, round(avg(p95_span_duration_ms), 2) AS p95_latency_ms FROM gold.gold_system_observability_daily WHERE metric_date >= today() - 30 GROUP BY service_name, source_app ) ORDER BY p95_latency_ms DESC, error_rate_pct DESC LIMIT 20
  ```

### GS-019 — Phân bổ latency của các interaction theo khoảng thời gian (0-100ms, 100-500ms, 500-1000ms, >1000ms)

- **Category**: interaction_detail | **Difficulty**: hard
- **Status**: success
- **Table Match**: ❌ Expected any of ['fct_llm_interactions'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ❌ Found: ['latency_ms'] | Missing: ['CASE', 'WHEN']
- **Visualization**: ❌ Expected: bar, Got: table
- **Row Count**: 1
- **Generated SQL**:
  ```sql
  SELECT metric_date, round(avg(avg_latency_ms), 2) AS avg_latency_ms, round(avg(p95_latency_ms), 2) AS p95_latency_ms FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY metric_date ORDER BY metric_date LIMIT 100
  ```

### GS-020 — Danh sách tất cả source app trong hệ thống

- **Category**: app_management | **Difficulty**: easy
- **Status**: needs_clarification
- **Table Match**: ❌ Expected any of ['source_apps'], got []
- **SQL Keywords**: ❌ No SQL was generated
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 0

### GS-021 — Có bao nhiêu app đang hoạt động theo từng domain?

- **Category**: app_management | **Difficulty**: medium
- **Status**: needs_clarification
- **Table Match**: ❌ Expected any of ['source_apps'], got []
- **SQL Keywords**: ❌ No SQL was generated
- **Visualization**: ❌ Expected: bar, Got: table
- **Row Count**: 0

### GS-022 — Kết quả kiểm tra chất lượng dữ liệu gần nhất

- **Category**: data_quality | **Difficulty**: easy
- **Status**: needs_clarification
- **Table Match**: ❌ Expected any of ['data_quality_results'], got []
- **SQL Keywords**: ❌ No SQL was generated
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 0

### GS-024 — Trạng thái các batch ingestion gần đây

- **Category**: ingestion | **Difficulty**: easy
- **Status**: success
- **Table Match**: ❌ Expected any of ['raw_ingestion_batches'], got ['gold.gold_ingestion_health_daily', 'ops.agent_query_audit']
- **SQL Keywords**: ❌ Found: ['ORDER BY'] | Missing: ['status', 'batch_id']
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 4
- **Generated SQL**:
  ```sql
  SELECT source_app, batch_count, failed_batch_count, quarantined_batch_count, loaded_row_count, round(failed_batch_count / greatest(batch_count, 1) * 100, 2) AS failed_batch_rate_pct FROM ( SELECT source_app, sum(batch_count) AS batch_count, sum(failed_batch_count) AS failed_batch_count, sum(quarantined_batch_count) AS quarantined_batch_count, sum(loaded_row_count) AS loaded_row_count FROM gold.gold_ingestion_health_daily WHERE metric_date >= today() - 30 GROUP BY source_app ) ORDER BY failed_batch_count DESC, quarantined_batch_count DESC LIMIT 20
  ```

### GS-025 — Tổng số dòng đã nạp thành công theo từng source app

- **Category**: ingestion | **Difficulty**: medium
- **Status**: error
- **Table Match**: ❌ Expected any of ['raw_ingestion_batches'], got []
- **SQL Keywords**: ❌ No SQL was generated
- **Visualization**: ❌ Expected: bar, Got: table
- **Row Count**: 0

### GS-026 — So sánh chi phí và tỷ lệ lỗi theo domain

- **Category**: cross_analysis | **Difficulty**: hard
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ❌ Found: ['total_cost_usd', 'GROUP BY'] | Missing: ['domain', 'error_rate']
- **Visualization**: ❌ Expected: bar, Got: line
- **Row Count**: 13
- **Generated SQL**:
  ```sql
  SELECT metric_date, model, round(sum(total_cost_usd), 6) AS total_cost_usd FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY metric_date, model ORDER BY metric_date, model LIMIT 100
  ```

### GS-027 — Mối quan hệ giữa số token sử dụng và chi phí theo model (scatter plot)

- **Category**: cross_analysis | **Difficulty**: hard
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost', 'agg_daily_llm_metrics'], got ['vsf_mart.fct_llm_interactions']
- **SQL Keywords**: ✅ Found: ['total_tokens', 'total_cost_usd', 'model']
- **Visualization**: ✅ Expected: scatter, Got: scatter
- **Row Count**: 13
- **Generated SQL**:
  ```sql
  SELECT model,
       SUM(total_tokens) AS total_tokens,
       SUM(total_cost_usd) AS total_cost_usd
FROM vsf_mart.fct_llm_interactions
WHERE model ILIKE '%' 
GROUP BY model
ORDER BY total_tokens DESC
LIMIT 100
  ```

### GS-028 — Xu hướng chi phí và số request theo ngày trong 7 ngày gần nhất

- **Category**: time_analysis | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ❌ Found: ['date', 'total_cost_usd'] | Missing: ['request_count']
- **Visualization**: ❌ Expected: line, Got: table
- **Row Count**: 0
- **Generated SQL**:
  ```sql
  SELECT metric_date, round(sum(total_cost_usd), 6) AS total_cost_usd FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 7 GROUP BY metric_date ORDER BY metric_date LIMIT 100
  ```

### GS-029 — Số lượng log ERROR theo service trong tuần qua

- **Category**: log_analysis | **Difficulty**: medium
- **Status**: success
- **Table Match**: ❌ Expected any of ['signoz_logs', 'stg_signoz_logs'], got ['gold.gold_system_observability_daily']
- **SQL Keywords**: ❌ Found: ['ERROR', 'service_name', 'GROUP BY'] | Missing: ['severity']
- **Visualization**: ✅ Expected: bar, Got: bar
- **Row Count**: 4
- **Generated SQL**:
  ```sql
  SELECT service_name, sum(log_count) AS log_count, sum(error_log_count) AS error_log_count, sum(span_count) AS span_count, sum(error_span_count) AS error_span_count, round(avg(avg_span_duration_ms), 2) AS avg_span_duration_ms, round(avg(p95_span_duration_ms), 2) AS p95_span_duration_ms FROM gold.gold_system_observability_daily WHERE metric_date >= today() - 7 GROUP BY service_name ORDER BY error_span_count DESC, error_log_count DESC LIMIT 100
  ```

### GS-030 — Liệt kê các log lỗi gần đây kèm trace_id để debug

- **Category**: log_analysis | **Difficulty**: hard
- **Status**: success
- **Table Match**: ❌ Expected any of ['signoz_logs', 'stg_signoz_logs'], got ['silver.silver_system_logs']
- **SQL Keywords**: ✅ Found: ['severity', 'ERROR', 'trace_id', 'message', 'ORDER BY']
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 0
- **Generated SQL**:
  ```sql
  SELECT event_time, source_app, service_name, severity, message, trace_id, span_id FROM silver.silver_system_logs WHERE event_time >= now() - INTERVAL 30 DAY AND lower(severity) IN ('error', 'fatal', 'critical') AND (positionCaseInsensitiveUTF8(source_app, 'trace_id') > 0 OR positionCaseInsensitiveUTF8(source_app, 'trace-id') > 0 OR positionCaseInsensitiveUTF8(service_name, 'trace_id') > 0 OR positionCaseInsensitiveUTF8(service_name, 'trace-id') > 0) ORDER BY event_time DESC LIMIT 100
  ```

## ✅ Passed Cases — Generated SQL

<details><summary>GS-001 — Tổng chi phí LLM của tất cả ứng dụng là bao nhiêu?</summary>

```sql
SELECT sum(total_cost_usd) AS total_cost_usd FROM vsf_mart.gold_app_daily_cost LIMIT 100
```
- Rows: 1 | Latency: 31067ms | Viz: Expected: table, Got: table
</details>

<details><summary>GS-014 — Phân tích tỷ lệ phân bổ request giữa các provider dưới dạng </summary>

```sql
SELECT provider AS provider,
       sum(request_count) AS request_count
FROM vsf_mart.gold_app_daily_cost
WHERE date >= subtractDays(today(), 30)
GROUP BY provider
LIMIT 500
```
- Rows: 4 | Latency: 84473ms | Viz: Expected: pie, Got: pie
</details>

<details><summary>GS-015 — So sánh hiệu suất giữa gpt-4o và gemini-2.0-flash về chi phí</summary>

```sql
SELECT
    g.model AS model,
    sum(g.total_cost_usd) AS cost_usd,
    avg(g.avg_latency_ms) AS avg_latency_ms
FROM vsf_mart.gold_app_daily_cost AS g
WHERE g.model ILIKE 'gpt-4o' OR g.model ILIKE 'gemini-2.0-flash'
  AND g.date >= subtractDays(today(), 30)
GROUP BY g.model
ORDER BY g.model
LIMIT 100
```
- Rows: 2 | Latency: 67697ms | Viz: Expected: bar, Got: table
</details>

<details><summary>GS-023 — Bao nhiêu test data quality đang pass và fail?</summary>

```sql
SELECT status AS status,
       count(*) AS request_count
FROM vsf_mart.data_quality_results
WHERE executed_at >= subtractDays(today(), 30)
GROUP BY status
LIMIT 100
```
- Rows: 3 | Latency: 42169ms | Viz: Expected: pie, Got: bar
</details>

