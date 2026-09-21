# ACT Sales 架构审视（BEFORE）：从固定报表到 Metric + Dimension + Filter 引擎

> 本文是 `spike/sales-semantic-layer-demo` 的 **audit 快照**，记录动手前的真实结构。
> 基线：`hefendadi/CCG-WorkPanel-open` @ `0c3f084`（public repo，`main`）。
> 所有行数均为该 commit 上的实测值，非估算。

---

## 1. 现状结构

ACT Sales 目前是「一个视图 = 一套专用实现」的形态，四类分析视图分别落在四层硬编码上。

```
webapp/sales/dashboard_query_service.py   517 lines   ← 每个视图一个 query function
webapp/sales/api.py                       561 lines   ← 每个视图一个 endpoint + 参数校验
webapp/static/js/sales-dashboard.js       310 lines   ← 每个视图一段专用渲染
webapp/static/sales-actual.html           117 lines
webapp/static/sales-actual.css            132 lines
```

### 1.1 数据层：`dashboard_query_service.py`

| 函数 | 行数 | 作用 |
|---|---:|---|
| `_aggregate_page()` | 52 | 所有视图共享的聚合 + 分页骨架 |
| `get_filter_options()` | 43 | 下拉选项（fact snapshot distinct） |
| `_matrix()` | 32 | Channel/Rep × Product 的共用辅助（只有这一处被复用） |
| `get_product_aggregation()` | 30 | Summary 之外的 Product 聚合 |
| `get_product_sku_drilldown()` | 27 | Product → SKU 下钻（带 `product_id` 过滤） |
| `get_summary()` | 18 | MTD 总量 / product 数 / sku 数 |
| `get_salesrep_product()` | 13 | SalesRep × Product 矩阵 |
| `get_channel_product()` | 11 | Channel × Product 矩阵 |

注意：`get_salesrep_product` 与 `get_channel_product` 只有 11/13 行，是因为它们把逻辑
塞进了 `_matrix()`。**这个 `_matrix()` 本身就是一个"手写的、只能处理两个维度的迷你引擎"** —
它写死了 `dimension`、`dimension_name`、`id_key`、`name_key` 四个参数，且永远带上 `product`。
也就是说：现有代码其实已经在向通用化靠拢，只是没有抽象成 registry。

### 1.2 API 层：`api.py` 中的 dashboard endpoints

```
GET /dashboard/context                      → dashboard_context
GET /dashboard/summary                      → dashboard_summary
GET /dashboard/filter-options                → dashboard_filter_options
GET /dashboard/products                      → dashboard_products
GET /dashboard/products/{product_id}/skus    → dashboard_product_skus
GET /dashboard/salesrep-products             → dashboard_salesrep_products
GET /dashboard/channel-products              → dashboard_channel_products
```

7 个 endpoint，每个都要重复：Query 参数声明、`DashboardFilters` 组装、
`_dashboard_call()` 错误映射、`_api_share()` share 字段改名、分页参数解析（`_pagination`）、
排序白名单（`_sort`）。

### 1.3 前端层：`sales-dashboard.js`

```
renderOptions / renderPager / renderSummary / renderProducts / renderMatrix
loadData()   ← 一次性并发拉 3 个矩阵 endpoint
```

`renderMatrix(id,data,key,label)` 已经是一个"按 key/label 渲染矩阵"的通用函数，
但它的**表头、列定义、列顺序、维度名称**仍然来自调用点硬编码，
而且每个视图对应一次独立的 `request()` 调用。

---

## 2. 增加一种新分析视图，当前需要改多少处？

以「新增 Customer × Product 视图」为例：

| 层 | 需要新增/修改 | 说明 |
|---|---|---|
| Query/Service | 1 个新函数（约 12 行） | `get_customer_product()`，或再给 `_matrix()` 加一个 `dimension` 分支 |
| API endpoint | 1 个新 endpoint + 参数声明（约 12 行） | `dashboard_customer_products`，含 `DashboardFilters` 与 `_dashboard_call` |
| Filter/Sort 逻辑 | 2 处以上 | `_dashboard_filters` 增加 customer 维度；`_sort` 白名单增加可用排序键；`get_filter_options` 增加 customer 选项 |
| 前端渲染 | 3 处 | `loadData()` 增加一次 request；新增 `renderCustomer()` 或扩展 `renderMatrix` 调用点；HTML 增加一块 `section` + 表头 |
| 测试/契约 | 1 组 | 新 endpoint 的契约测试与分页测试 |

**结论：一个"只是多一个维度"的需求，需要跨 4 层、至少 8~10 处改动。**

真正的问题不是单次改动量大，而是：

1. **组合爆炸**：Product/Channel/SalesRep/Customer/SKU 两两组合就有 10 种；
   现有 `_matrix()` 只能表达「某个 ref × Product」，三维度组合（Channel × SalesRep × Product）
   完全没有对应实现。
2. **契约漂移**：每个 endpoint 的 `share_denominator`、排序键名、分页字段都可能不一致。
3. **前端重复**：同一张表格在不同视图里各写一遍 `<th>` 与列渲染。

---

## 3. 已经被验证的好基础（不能破坏）

这些约束是 spike 必须继承的，而不是推翻的：

1. **Snapshot 语义**：`SalesFact` 上的 `*_snapshot` 列是 Publish 时冻结的归属。
   历史月份不能被当前 MDM 重新解释。`dashboard_query_service` 的模块 docstring 明确写了
   "Snapshot dimensions on Fact are authoritative; Candidate and MDM tables are
   intentionally outside this service"。
2. **Batch 守卫**：`_validated_batch()` 保证
   - 请求的 `batch_id` 存在；
   - 它是该 `(source_system, snapshot_month)` 下**唯一** current Published batch
     （`PUBLISHED` 且 `replaced_at IS NULL`）；
   - 整月 Fact 行数/数量与 `batch.ready_rows/ready_qty` 完全一致（`_validate_fact_integrity`）。
3. **未归属语义**：`channel_id_snapshot` / `salesrep_id_snapshot` 为 NULL 时显示 `未归属`，
   且 NULL 必须能被筛选（`_dimension_filter` 显式处理 `None in values`）。
4. **share 语义**：`share = group_qty / filtered_total`，分母是**当前筛选条件下的总量**，
   不是全量。
5. **权限**：`MODULE_SALES_ACTUAL` + `VIEW`（API）与 `require_page_permission`（页面）。

---

## 4. 目标：Metric + Dimension + Filter

```
        ResourceSpec（现有 MDM spike 已经证明过的模式）
                        ↓
   MetricSpec + DimensionSpec + DatasetSpec
                        ↓
             Generic Query Engine
      （唯一入口 POST /api/v1/lab/sales/query）
                        ↓
         Explorer UI（元数据驱动，零 per-view 代码）
```

新增一种视图 = 在 registry 里组合已有的 Metric 与 Dimension。
**不新增** query function、endpoint、filter 映射或前端渲染分支。

### 4.1 第一版范围（刻意收窄）

- Dataset：`sales_actual` 一个。
- Metric：只有 `actual_qty`（`SUM(sales_actual.actual_qty)`）。不做 distinct/ratio/派生指标。
- Dimensions：`product`、`sku`、`channel`、`salesrep`、`customer`、`sales_date`、`snapshot_month`。
- Guardrails：metric/dimension/filter 白名单、最多 3 个 group-by、最多 500 行、
  确定性排序、`VIEW` 权限、current Published batch 校验。

### 4.2 明确不做（并在架构页面上说明）

- **不做 Category 维度**。`SalesFact` 没有 Category snapshot 列。
  任何「用当前 MDM JOIN 出历史 Category」的做法都是伪造历史归属，
  必须等 snapshot policy 决策（见 `sales-semantic-layer-demo.md` 的 Category 章节）。
- 不为了 Demo 修改任何正式 query function、API 契约或 schema。
- 不引入 SQL 文本、SQLAlchemy 对象或物理列名到 HTTP 契约里。

---

## 5. 预期收益（可验证，不靠感觉）

| 维度 | BEFORE | AFTER |
|---|---|---|
| 新增两维组合视图 | 4 层 × 8~10 处改动 | registry 加 1 个 DimensionSpec |
| 三维组合（如 Channel × SalesRep × Product） | 无实现，需新写 | 现有引擎直接支持 |
| 前端渲染 | 每视图一段 | 一份元数据驱动表格 |
| 端点数 | 7 个 dashboard endpoint | 1 个执行 endpoint + 2 个元数据 endpoint |
| 契约一致性 | 每 endpoint 各自维护 | 单一 registry 派生 |

下一步：`sales-semantic-layer-demo.md`（AFTER 详细设计、兼容性矩阵与结论）。
