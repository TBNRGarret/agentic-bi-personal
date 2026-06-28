# 🎯 Agent Golden Set — Evaluation Report

**Generated at**: 2026-06-25 11:03 UTC  
**Total execution time**: 140.7s  
**Test cases**: 30

## 📊 Overall Metrics

| Metric | Score | Rate |
|:---|:---:|:---:|
| Table Selection Accuracy | 0/30 | 0% |
| SQL Generation | 1/30 | 3% |
| SQL Execution Success | 1/30 | 3% |
| SQL Keywords Match | 1/30 | 3% |
| Visualization Match | 1/30 | 3% |
| **Full Pass** (table + exec + kw) | **0/30** | **0%** |
| Average Latency | 4691ms | — |

## 📂 Results by Category

| Category | Total | Table | Exec | Keywords | Viz | Full Pass |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| app_management | 2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| cost_analysis | 4 | 0/4 | 1/4 | 1/4 | 1/4 | 0/4 |
| cross_analysis | 2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| data_quality | 2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| error_analysis | 3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| ingestion | 2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| interaction_detail | 3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| log_analysis | 2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| model_comparison | 2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| performance | 3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| time_analysis | 1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 |
| usage | 4 | 0/4 | 0/4 | 0/4 | 0/4 | 0/4 |

## 🏷️ Results by Difficulty

| Difficulty | Total | Full Pass | Rate |
|:---|:---:|:---:|:---:|
| 🟢 easy | 8 | 0/8 | 0% |
| 🟡 medium | 14 | 0/14 | 0% |
| 🔴 hard | 8 | 0/8 | 0% |

## 📋 Detailed Results

| # | ID | Difficulty | Question | Table | Exec | KW | Viz | Latency | Status |
|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | GS-001 | 🟢 | Tổng chi phí LLM của tất cả ứng dụng là bao nhiêu? | ❌ | ✅ | ✅ | ✅ | 69973ms | success |
| 2 | GS-002 | 🟡 | So sánh chi phí LLM theo từng provider | ❌ | ❌ | ❌ | ❌ | 70736ms | exception |
| 3 | GS-003 | 🟡 | Chi phí LLM theo ngày trong tuần qua | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 4 | GS-004 | 🔴 | Top 3 model tốn chi phí nhiều nhất theo từng sourc… | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 5 | GS-005 | 🟢 | Độ trễ trung bình của các request LLM là bao nhiêu… | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 6 | GS-006 | 🟡 | So sánh p95 latency giữa các model | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 7 | GS-007 | 🟡 | Xu hướng latency trung bình theo ngày | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 8 | GS-008 | 🟢 | Tỷ lệ lỗi tổng thể của hệ thống là bao nhiêu? | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 9 | GS-009 | 🟡 | Tỷ lệ lỗi theo từng model LLM | ❌ | ❌ | ❌ | ❌ | 3ms | exception |
| 10 | GS-010 | 🔴 | Liệt kê các interaction bị lỗi gần đây kèm chi tiế… | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 11 | GS-011 | 🟢 | Tổng số request LLM trong hệ thống là bao nhiêu? | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 12 | GS-012 | 🟡 | Số lượng request theo từng source app | ❌ | ❌ | ❌ | ❌ | 0ms | exception |
| 13 | GS-013 | 🟡 | Xu hướng số lượng token sử dụng theo ngày | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 14 | GS-014 | 🔴 | Phân tích tỷ lệ phân bổ request giữa các provider … | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 15 | GS-015 | 🟡 | So sánh hiệu suất giữa gpt-4o và gemini-2.0-flash … | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 16 | GS-016 | 🔴 | Model nào có chi phí trung bình trên mỗi request t… | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 17 | GS-017 | 🟢 | Liệt kê 10 interaction gần nhất | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 18 | GS-018 | 🟡 | Những interaction nào tiêu tốn nhiều token nhất? | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 19 | GS-019 | 🔴 | Phân bổ latency của các interaction theo khoảng th… | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 20 | GS-020 | 🟢 | Danh sách tất cả source app trong hệ thống | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 21 | GS-021 | 🟡 | Có bao nhiêu app đang hoạt động theo từng domain? | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 22 | GS-022 | 🟢 | Kết quả kiểm tra chất lượng dữ liệu gần nhất | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 23 | GS-023 | 🟡 | Bao nhiêu test data quality đang pass và fail? | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 24 | GS-024 | 🟢 | Trạng thái các batch ingestion gần đây | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 25 | GS-025 | 🟡 | Tổng số dòng đã nạp thành công theo từng source ap… | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 26 | GS-026 | 🔴 | So sánh chi phí và tỷ lệ lỗi theo domain | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 27 | GS-027 | 🔴 | Mối quan hệ giữa số token sử dụng và chi phí theo … | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 28 | GS-028 | 🟡 | Xu hướng chi phí và số request theo ngày trong 7 n… | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 29 | GS-029 | 🟡 | Số lượng log ERROR theo service trong tuần qua | ❌ | ❌ | ❌ | ❌ | 1ms | exception |
| 30 | GS-030 | 🔴 | Liệt kê các log lỗi gần đây kèm trace_id để debug | ❌ | ❌ | ❌ | ❌ | 1ms | exception |

## ❌ Failed Cases Detail

### GS-001 — Tổng chi phí LLM của tất cả ứng dụng là bao nhiêu?

- **Category**: cost_analysis | **Difficulty**: easy
- **Status**: success
- **Table Match**: ❌ Expected any of ['gold_app_daily_cost'], got ['gold.gold_llm_usage_daily']
- **SQL Keywords**: ✅ Found: ['SUM', 'total_cost_usd']
- **Visualization**: ✅ Expected: table, Got: table
- **Row Count**: 87
- **Generated SQL**:
  ```sql
  SELECT metric_date, model, round(sum(total_cost_usd), 6) AS total_cost_usd FROM gold.gold_llm_usage_daily WHERE metric_date >= today() - 30 GROUP BY metric_date, model ORDER BY metric_date, model LIMIT 100
  ```

### GS-002 — So sánh chi phí LLM theo từng provider

- **Category**: cost_analysis | **Difficulty**: medium
- **Status**: exception
- **Error**: `Server disconnected without sending a response.`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-003 — Chi phí LLM theo ngày trong tuần qua

- **Category**: cost_analysis | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-004 — Top 3 model tốn chi phí nhiều nhất theo từng source app

- **Category**: cost_analysis | **Difficulty**: hard
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-005 — Độ trễ trung bình của các request LLM là bao nhiêu?

- **Category**: performance | **Difficulty**: easy
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-006 — So sánh p95 latency giữa các model

- **Category**: performance | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-007 — Xu hướng latency trung bình theo ngày

- **Category**: performance | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-008 — Tỷ lệ lỗi tổng thể của hệ thống là bao nhiêu?

- **Category**: error_analysis | **Difficulty**: easy
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-009 — Tỷ lệ lỗi theo từng model LLM

- **Category**: error_analysis | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-010 — Liệt kê các interaction bị lỗi gần đây kèm chi tiết lỗi

- **Category**: error_analysis | **Difficulty**: hard
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-011 — Tổng số request LLM trong hệ thống là bao nhiêu?

- **Category**: usage | **Difficulty**: easy
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-012 — Số lượng request theo từng source app

- **Category**: usage | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-013 — Xu hướng số lượng token sử dụng theo ngày

- **Category**: usage | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-014 — Phân tích tỷ lệ phân bổ request giữa các provider dưới dạng biểu đồ tròn

- **Category**: usage | **Difficulty**: hard
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-015 — So sánh hiệu suất giữa gpt-4o và gemini-2.0-flash về chi phí và latency

- **Category**: model_comparison | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-016 — Model nào có chi phí trung bình trên mỗi request thấp nhất?

- **Category**: model_comparison | **Difficulty**: hard
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-017 — Liệt kê 10 interaction gần nhất

- **Category**: interaction_detail | **Difficulty**: easy
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-018 — Những interaction nào tiêu tốn nhiều token nhất?

- **Category**: interaction_detail | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-019 — Phân bổ latency của các interaction theo khoảng thời gian (0-100ms, 100-500ms, 500-1000ms, >1000ms)

- **Category**: interaction_detail | **Difficulty**: hard
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-020 — Danh sách tất cả source app trong hệ thống

- **Category**: app_management | **Difficulty**: easy
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-021 — Có bao nhiêu app đang hoạt động theo từng domain?

- **Category**: app_management | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-022 — Kết quả kiểm tra chất lượng dữ liệu gần nhất

- **Category**: data_quality | **Difficulty**: easy
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-023 — Bao nhiêu test data quality đang pass và fail?

- **Category**: data_quality | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-024 — Trạng thái các batch ingestion gần đây

- **Category**: ingestion | **Difficulty**: easy
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-025 — Tổng số dòng đã nạp thành công theo từng source app

- **Category**: ingestion | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-026 — So sánh chi phí và tỷ lệ lỗi theo domain

- **Category**: cross_analysis | **Difficulty**: hard
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-027 — Mối quan hệ giữa số token sử dụng và chi phí theo model (scatter plot)

- **Category**: cross_analysis | **Difficulty**: hard
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-028 — Xu hướng chi phí và số request theo ngày trong 7 ngày gần nhất

- **Category**: time_analysis | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-029 — Số lượng log ERROR theo service trong tuần qua

- **Category**: log_analysis | **Difficulty**: medium
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0

### GS-030 — Liệt kê các log lỗi gần đây kèm trace_id để debug

- **Category**: log_analysis | **Difficulty**: hard
- **Status**: exception
- **Error**: `[Errno 111] Connection refused`
- **Table Match**: ❌ 
- **SQL Keywords**: ❌ 
- **Visualization**: ❌ 
- **Row Count**: 0
