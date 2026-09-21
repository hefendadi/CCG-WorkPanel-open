# ACT Sales Semantic Layer Spike（AFTER）：设计与评审报告

> 分支 `spike/sales-semantic-layer-demo`，基于 `hefendadi/CCG-WorkPanel-open` @ `0c3f084`。
> **隔离 Demo / Architecture Spike**，不是正式功能开发。
> 全部数据为 **SYNTHETIC / DEMO**。

---

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| Metric + Dimension + Filter 是否可表达现有 4 个视图 | 是，且 total qty 与正式查询**完全一致** |
| 是否复用同一个 endpoint | 是：`POST /api/v1/lab/sales/query` |
| 是否新增 per-view query function / endpoint | 否 |
| 是否修改正式 Sales logic / schema / migration | 否 |
| 是否为了 Demo JOIN 当前 MDM | 否，snapshot-only |
| Category 维度 | NOT_SUPPORTED（等 snapshot policy 决策） |
| 测试 | 99 passed / 0 failed（含兼容性、白名单、权限、前端 smoke） |

推荐：**ADOPT_WITH_CHANGES**（见 §11）。

---

## 2. 分层结构

```
webapp/sales/semantic_registry.py        声明层：MetricSpec / DimensionSpec / DatasetSpec / PresetSpec
webapp/sales/semantic_query_engine.py    执行层：校验 → 构建 SQLAlchemy Query → 执行
webapp/sales/semantic_api.py             HTTP 层：GET /semantic, POST /query, GET /filter-options
webapp/static/sales-semantic-demo.html   Explorer + Architecture 页面
webapp/static/js/sales-semantic-demo.js  元数据驱动前端（无 per-view 逻辑）
```

数据流：

```
POST /api/v1/lab/sales/query
        ↓  validate_query()        白名单 + 上限 + 类型/算子校验
   SemanticQuery（冻结的、已校验的请求）
        ↓  run_query()             registry → 物理列映射 → GROUP BY
   items[] + total_qty + plan{...}
```

---

## 3. MetricSpec（第一版：1 个）

| key | label | 聚合 | 物理列 | format | unit |
|---|---|---|---|---|---|
| `actual_qty` | Sales Qty | `SUM` | `actual_qty` | decimal | Pcs |

对外 metadata 只暴露 `key / label / description / aggregation / format / unit`。

**刻意不暴露**：物理列名、`SalesFact`、任何 SQLAlchemy 对象或 SQL 片段。
（`test_public_metadata_never_leaks_sql_or_physical_columns` 断言这一点。）

---

## 4. DimensionSpec（第一版：7 个）

| key | label | type | role | group 策略 | filterable | nullable |
|---|---|---|---|---|---|---|
| `product` | Product | integer | reference | snapshot id + name label | 是 | 否 |
| `sku` | SKU | integer | reference | snapshot id + name label (+code) | 是 | 否 |
| `channel` | Channel | integer | reference | snapshot id + name label | 是 | **是** |
| `salesrep` | SalesRep | integer | reference | snapshot id + name label | 是 | **是** |
| `customer` | Customer | integer | reference | snapshot id（无 name snapshot） | 是 | **是** |
| `sales_date` | Sales Date | date | date | fact column | 是 | 否 |
| `snapshot_month` | Snapshot Month | month | month | fact column | 是 | 否 |

- 对外 metadata 暴露：`key / label / description / type / role / groupable / filterable / sortable / nullable / operators`。
- `operators` 由 `type` 派生：`string → eq,in`；`integer → eq,in,gte,lte,between`；
  `date/month → eq,gte,lte,between`。**客户端无法为某个维度指定它类型不支持的算子。**
- 未知维度（如 `category_l1`）在 `validate_query` 阶段直接 422。

### 4.1 一个必须记录的实现事实

public repo 的 `SalesFact` 只有 `sku_code_snapshot`，**没有** `channel_code_snapshot` /
`salesrep_code_snapshot` / `customer_code_snapshot` / `customer_name_snapshot`。
本 spike 严格按实际 schema 实现：Customer 维度只能以 surrogate id 作为 label。
这不是设计选择，而是 schema 现状；若将来需要 Customer 名称/编码，需要 snapshot 扩列，
同样属于 snapshot policy 议题。

---

## 5. Generic Query Engine

`validate_query(request) -> SemanticQuery` 完成后，`run_query()` 才构建查询。

- 聚合：`SUM(actual_qty)`
- 分组：每个维度按 `group_strategy` 产出 1~2 个 group 列（id + name label）
- 过滤：`_filter_column(dimension)` **始终落到该维度的 snapshot 列**
- 排序：请求的 metric 优先，**然后所有 group key 升序**作为确定性 tiebreak
- `total_qty`：同一 scope 下的无分组总量（用于与正式 dashboard 对比）
- `plan`：供开发评审的逻辑计划（无 SQL 文本）

### 5.1 可表达的组合（实测）

| 请求 | grouped rows | total qty |
|---|---:|---:|
| `actual_qty` + product | 3 | 35 |
| `actual_qty` + channel + product | 5 | 35 |
| `actual_qty` + salesrep + product | 5 | 35 |
| `actual_qty` + product + sku | 4 | 35 |
| `actual_qty` + customer | 4 | 35 |
| `actual_qty` + channel + salesrep + product | 6 | 35 |
| `actual_qty`（无维度，grand total） | 1 | 35 |

> 数字来自 `webapp/sales/tests/fixtures.py` 的 6 行 synthetic demo 数据。

---

## 6. Request / Response 契约

请求（`extra="forbid"`，任何多余字段 422）：

```json
{
  "dataset": "sales_actual",
  "batch_id": "……",
  "metrics": ["actual_qty"],
  "dimensions": ["channel", "product"],
  "filters": [
    {"dimension": "channel", "operator": "in", "values": [4031]},
    {"dimension": "sales_date", "operator": "between", "values": ["2000-02-01","2000-02-28"]}
  ],
  "sort": [{"field": "actual_qty", "direction": "desc"}],
  "limit": 100,
  "offset": 0
}
```

响应：

```json
{
  "dataset": "sales_actual",
  "batch_id": "……",
  "snapshot_month": "2000-02-01",
  "metrics": [...], "dimensions": [...],
  "items": [{"channel": 4031, "channel_name": "Demo Channel A",
             "product": 2001, "product_name": "Demo Product 1",
             "actual_qty": "17.0000"}],
  "total_qty": "35.0000",
  "plan": {"snapshot_semantics": "FACT_SNAPSHOT_ONLY", ...},
  "pagination": {"offset":0,"limit":100,"total_group_rows":5,
                 "returned_rows":5,"truncated":false,"max_result_rows":500}
}
```

---

## 7. Guardrails（全部有测试）

| Guardrail | 实现 | 测试 |
|---|---|---|
| metric 白名单 | `DatasetSpec.metric()` | `test_metrics_must_be_whitelisted` |
| dimension 白名单 | `DatasetSpec.dimension()` | `test_dimensions_must_be_whitelisted` |
| filter 白名单 | 维度 + 算子双重校验 | `test_filter_operator_must_be_whitelisted` 等 |
| 最多 3 个 group-by | `DatasetSpec.max_group_by` | `test_group_by_is_capped_at_three_dimensions` |
| 最大结果行数 | `max_rows=500` | `test_limit_is_capped_at_max_result_rows` |
| 确定性排序 | metric + 全 group key 升序 | `test_ordering_is_deterministic_across_identical_calls` |
| `VIEW` 权限 | `require_permission(MODULE_SALES_ACTUAL, VIEW)` | `test_unauthenticated_calls_are_rejected` |
| current Published batch | 复用 `_validated_batch()` | `test_replaced_batch_is_rejected` 等 |
| 请求字段白名单 | Pydantic `extra="forbid"` + 引擎字段校验 | `test_unknown_request_fields_are_rejected` |
| 抗注入 | 不接收 SQL/列名/表达式 | `test_sql_fragments_are_rejected_as_metric_names` |

**绝对禁止且已测试**：arbitrary SQL、arbitrary column names、arbitrary model fields、
客户端传入 SQL expression。引擎只从 registry 取 key，再从模块私有的 `_FACT_COLUMNS`
映射取 SQLAlchemy 属性——客户端字符串永不直接参与 SQL 构建。

---

## 8. 兼容性矩阵

比较对象：`get_product_aggregation` / `get_channel_product` / `get_salesrep_product` /
`get_product_sku_drilldown`。**未修改任何正式 query function。**

| 现有视图 | 语义等价请求 | 分类 | 证据 |
|---|---|---|---|
| Summary（MTD 总量） | `metrics=[actual_qty]`, `dimensions=[]` | **COMPATIBLE** | total 均为 35 |
| Product aggregation | `dimensions=[product]` | **COMPATIBLE** | 同 key、同 qty、同默认排序；`share`/`sku_count` 缺失见下 |
| Channel × Product | `dimensions=[channel,product]` | **COMPATIBLE** | 同 (channel_id, product_id) 键与 qty，含 `未归属` 行 |
| SalesRep × Product | `dimensions=[salesrep,product]` | **COMPATIBLE** | 同 (salesrep_id, product_id) 键与 qty |
| Product → SKU drilldown | `dimensions=[sku]` + `product` filter | **COMPATIBLE** | 逐 product 比对 sku→qty 完全一致 |
| Product aggregation 的 `share` | 需派生（group/total） | **INTENTIONALLY_DIFFERENT** | 第一版只有 additive metric；share 不是 metric 而是派生展示 |
| Product aggregation 的 `sku_count` | `dimensions=[product,sku]` 计数可得 | **INTENTIONALLY_DIFFERENT** | 同上，非 additive metric；已用测试证明可从引擎结果推导出相同值 |
| Customer / Sales Date / Snapshot Month | — | **NEW（无正式对应视图）** | 语义引擎免费获得，正式端无实现 |
| Channel × SalesRep（× Product） | — | **NEW** | 正式端无实现 |
| Category L1~L4 | — | **NOT_SUPPORTED** | 无 snapshot 列，等决策 |

**最重要的不变式**：同一 batch / filter 条件下，
`total actual_qty` 在所有 4 个正式查询与语义引擎之间**完全一致**，
由 `test_total_actual_qty_matches_summary_for_every_view` 断言。

> 说明：第一版刻意只做 additive metric。`share`、`sku_count` 属于 ratio/count 类，
> 需要 MetricSpec 增加 `ratio`/`distinct_count` 聚合形态——那是有意的下一阶段，
> 不是本 spike 的缺口。

---

## 9. Presets：证明的关键

四个快捷按钮 **Product / Channel × Product / SalesRep × Product / Product × SKU**
只做一件事：修改 `metric` + `dimensions` 选择，然后调用同一个
`POST /api/v1/lab/sales/query`。它们**不调用**原有四个专用 Dashboard API。

前端 smoke 测试对此做了硬约束：
`test_script_never_calls_a_dedicated_dashboard_api`（禁止出现
`/dashboard/channel-products`、`/dashboard/salesrep-products` 等）
与 `test_script_uses_the_single_semantic_endpoints`（脚本中只有一个 `method: 'POST'`）。

---

## 10. Category 议题（不替用户决定）

用户未来关心 **Category × BP Channel**，但 `SalesFact` **没有 Category snapshot**。

本 spike 的处理：

1. 引擎层面 Category **不可查询**（`validate_query` 拒绝 `category_l1..l4`）。
2. Architecture 页面单独展示 `Future Dimensions: category_l1..l4`，
   status = `REQUIRES_SNAPSHOT_POLICY_DECISION`。
3. 视觉演示只使用明确标记 **SIMULATED** 的前端本地常量，不进入 Semantic Query Engine。

两个选项（Demo 不选边）：

- **Option A** — 销售 Publish 时冻结 Category snapshot。
  历史可复现、报表稳定；代价是新增 snapshot 列 + 修改 Publish 契约 + 历史回填策略。
- **Option B** — 历史销售永远按当前 MDM 重新分类。
  实现便宜；代价是**历史报表会随 MDM 变更而漂移**，与现有
  "Snapshot dimensions on Fact are authoritative" 原则冲突。

---

## 11. 影响评估与建议

### 代码量对比

| 项 | BEFORE | AFTER（spike） |
|---|---:|---:|
| 查询执行层 | 517 行 service（7 个视图函数 + `_matrix` + `_aggregate_page`） | 引擎约 640 行（含校验与 plan），支撑任意维度组合 |
| Dashboard endpoints | 7 个 | 执行 endpoint 1 个 + 元数据 2 个 |
| 前端每视图代码 | 每视图一段 `render*` | 一份元数据驱动表格 |
| 新增「Customer × Product」 | 4 层 × 8~10 处 | registry 加 1 个 DimensionSpec（已存在）→ 0 处 |
| 新增三维组合 | 无实现 | 已支持 |

> 诚实说明：**spike 的绝对行数不比现状少**。收益来自「新增视图的边际成本趋近于 0」，
> 而不是当前代码更短。真正的收敛要等 Phase 2/3 把正式 endpoint 下沉到引擎之后。

### 建议：ADOPT_WITH_CHANGES

采纳方向，但必须带以下变更后再进正式开发：

1. **先扩展 MetricSpec**：补 `ratio`（share）与 `distinct_count`（sku_count）聚合形态，
   否则无法完全替代 `get_product_aggregation` 的现有输出契约。
2. **Customer / Channel / SalesRep 的 code + name snapshot 补列**
   属于 snapshot policy 决策范围，必须与 Category 议题一并决策，不能各自为政。
3. **`snapshot_month` 维度语义要先定义**：当前 batch 只有一个月，
   该维度在 UI 上具有误导性，正式化前需要「跨月分析」的产品定义。
4. **不要立刻替换正式 endpoint**。按 `sales-semantic-before.md` §10 的三阶段路线：
   Phase 1 shadow lab（当前）→ Phase 2 hybrid route → Phase 3 full metadata。
5. **保持 snapshot-only 戒律**：任何把引擎接到 MDM JOIN 的改动都必须走 policy 评审。

---

## 12. 测试

```
webapp/sales/tests/test_semantic_registry.py       16 tests   声明层契约
webapp/sales/tests/test_semantic_query.py          25 tests   分组/过滤/排序/分页/batch 守卫
webapp/sales/tests/test_semantic_security.py       32 tests   白名单/guardrail/权限/无写入端点
webapp/sales/tests/test_semantic_compatibility.py  13 tests   与 4 个正式查询的兼容性
webapp/sales/tests/test_semantic_frontend.py       13 tests   页面与脚本契约
                                                 ─────────
                                                  99 tests   all passed
```

运行（无需 MySQL / Docker / 网络，使用内存 SQLite + synthetic fixture）：

```bash
python3 -m unittest \
  webapp.sales.tests.test_semantic_registry \
  webapp.sales.tests.test_semantic_query \
  webapp.sales.tests.test_semantic_security \
  webapp.sales.tests.test_semantic_compatibility \
  webapp.sales.tests.test_semantic_frontend
```

数据来源：`webapp/sales/tests/fixtures.py` 生成的 synthetic Published batch
（6 行、35 Pcs、含 1 行 NULL channel/salesrep/customer 用于验证 `未归属`）。
无 Production、无 private repo、无真实 Customer/SKU/Sales 数据。
