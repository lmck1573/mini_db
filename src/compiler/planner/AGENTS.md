# planner（执行计划生成器）开发规范

## 模块职责

把语义分析通过的 AST 翻译为**逻辑执行计划**（Plan 树），是编译器的最后一道工序（类比"目标代码生成"）。
两段式：`planner.py` 做结构翻译产出"原始计划"，`optimizer.py` 做**语义等价的规则改写**产出"优化后计划"。

**实现状态**：P0 + P1 + P2 亮点项已实现。文件 `plan_nodes.py`（10 类算子 + 三种文本输出 + `from_dict` + HTML 可视化）、`planner.py`（AST → 计划）、`optimizer.py`（4 条优化规则）、`visualize.py`（Plan 树 → 内联 SVG / 自包含 HTML）；测试 `tests/compiler/test_planner.py`、`test_optimizer.py`、`test_multitable.py`、`test_aggregate.py`、`test_explain_viz.py`。

## 范围与优先级

| 级别 | planner 对应内容 |
|------|-----------------|
| **P0 必做（已完成）** | 四类语句 → 标准形状计划（CreateTable / Insert / SeqScan+Filter / Project / Delete）；树形、JSON、S 表达式三种文本输出；`from_dict(to_dict())` 往返无损 |
| **P1 进阶（已实现）** | 4 条优化规则：常量折叠、恒真恒假简化、NOT 消除、Filter 消除；优化前后计划可对比输出 |
| **P2 亮点（已实现）** | 多表 JOIN 计划（INNER/LEFT/CROSS）、聚合 / GROUP BY / HAVING 计划（AggregatePlan）、ORDER BY 计划（SortPlan）、表达式投影、EXPLAIN（仅编译出计划不执行）、图形化 Plan 可视化（内联 SVG + 自包含 HTML） |
| **P2 扩展（不做）** | 代价模型驱动的规则框架、JOIN 重排（join reorder）、物理计划选择（索引 / 下推） |

**当前边界**：计划形状为 `Project → Sort → Filter/Aggregate → Join* → SeqScan`；**不做算子树重排**（无代价模型、不做 JOIN 重排）；
优化只做语义等价的谓词改写，不涉及物理信息（无索引、无代价估算）。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `Statement` + `Catalog` | 语义分析后的 AST |
| 输出 | `PlanNode` | 逻辑执行计划树 |

计划必须支持三种输出形式（课程要求任选，本模块全部提供）：

1. 树形结构（`to_tree()`）
2. JSON（`to_json()` / `to_dict()`）
3. S 表达式（`to_sexpr()`）

额外提供 `to_html()` / `write_plan_html()`（自包含 HTML + 内联 SVG），用于**计划可视化**。

## 接口定义

```python
class PlanNode:                       # 基类
    def to_dict(self) -> dict: ...
    def to_json(self, indent=2) -> str: ...
    def to_tree(self, prefix="") -> str: ...
    def to_sexpr(self) -> str: ...
    def to_html(self, title=...) -> str: ...
    @staticmethod
    def from_dict(d: dict) -> "PlanNode": ...

CreateTablePlan(table_name, columns: list[ColumnDef])
InsertPlan(table_name, columns: list[str] | None, rows: list[list[Literal]])
SeqScanPlan(table_name, source=None)          # source: 多表场景下的行键前缀（别名）
FilterPlan(predicate: Expr, child: PlanNode)
ProjectPlan(columns, child, exprs=None, labels=None)  # exprs/labels: 表达式投影
DeletePlan(table_name, predicate: Expr | None)

# —— P2 亮点新增算子 ——
JoinPlan(join_type, left, right, on, right_columns)   # INNER / LEFT / CROSS
AggregatePlan(group_by, group_labels, aggregates, child)  # aggregates: list[AggregateSpec]
SortPlan(keys: list[SortKey], child)                  # SortKey(expr, desc)

def build_plan(statement: Statement, catalog: Catalog) -> PlanNode: ...
    # 结构翻译，产出"未优化"的原始计划；遇不支持语法抛 PlannerError

def plan(statement: Statement, catalog: Catalog, optimize: bool = True) -> PlanNode: ...
    # 对外入口，默认返回优化后的计划

def plan_with_optimization(statement: Statement, catalog: Catalog
                           ) -> tuple[PlanNode, PlanNode, list[str]]:
    # 返回（原始计划, 优化后计划, 命中规则说明），用于演示优化前后差异
```

## 翻译规则

| 语句 | 计划结构 |
|------|---------|
| CREATE TABLE | `CreateTablePlan` |
| INSERT | `InsertPlan` |
| DELETE ... WHERE p | `DeletePlan(table, p)`（内部等价 SeqScan + Filter + 删除） |
| SELECT * FROM t | `ProjectPlan("*", SeqScanPlan(t))` |
| SELECT a,b FROM t | `ProjectPlan([a,b], SeqScanPlan(t))` |
| SELECT ... WHERE p | `ProjectPlan(cols, FilterPlan(p, SeqScanPlan(t)))` |
| SELECT ... FROM a JOIN b ON p | 左深 Join 链；`SeqScan(source=别名)` 产出 `别名.列` 行键，`JoinPlan(on=` 解析后的谓词 `)` |
| SELECT ... GROUP BY g | `AggregatePlan(group_by=[g], aggregates=[...], child)`，HAVING 转为其上的 `FilterPlan` |
| SELECT ... ORDER BY k | 最外层加 `SortPlan([SortKey(expr, desc)], child)` |
| EXPLAIN <stmt> | 复用同一套翻译，结果存入 `StatementResult.explain_plan`，**`plan` 置 None** 令 engine 跳过执行 |

**聚合投影改写**：`planner.py` 在构建 `AggregatePlan` 时产出 `rewrite` 映射（聚合表达式 → 聚合结果列标签）。
HAVING / ORDER BY / 投影中的聚合引用经 `rewrite` 统一改指向聚合结果列，保证计划中的数据流一致。

**JOIN 行键**：多表计划下行键一律为 `别名.列名`（由 `SeqScanPlan(source=别名)` 决定），
避免同名列冲突；`right_columns` 供 LEFT JOIN 在右侧无匹配时补 NULL。

不支持的语法或缺失语义信息时抛 `PlannerError`，格式 `[PlannerError, Line x Col y, 原因说明]`。

## EXPLAIN（仅编译不执行）

`compiler.py` 识别 `EXPLAIN` 语句时：
1. 依赖 Catalog 的**副本**做 dry run，正常走 semantic + planner，产出可执行计划；
2. 把计划写入 `StatementResult.explain_plan`，`explain=True`，并把 `plan` 保持为 `None`；
3. engine 见 `plan is None` 即跳过执行，天然实现"只出计划不跑数据"。

`StatementResult.display_plan` 属性统一返回"该展示的计划"（EXPLAIN 时取 `explain_plan`，否则取 `plan`）。

## 计划可视化（visualize.py）

| 函数 | 说明 |
|------|------|
| `plan_to_svg(node)` | 把 Plan 树渲染为内联 SVG（自左向右的树，算子按类型着色） |
| `plan_to_html(node, title=...)` | 生成**自包含** HTML（无外链 script/style/字体），含图例与悬停 JSON 提示 |
| `write_plan_html(node, path, ...)` | 落盘 `plan_to_html` 结果 |

- 自包含约束：不引用任何 `http(s)://` 资源（SVG 的 `xmlns` 除外），便于直接双击打开；
- 配色同时适配浅色 / 深色环境；`main.py --plan-format html --plan-out <path>` 可直接产出 HTML。

## 优化规则（optimizer.py）

优化在**原始计划**的谓词上做等价改写，不改动算子树结构：

| 规则名 | 说明 | 示例 |
|--------|------|------|
| `constant_folding` | 纯常量运算直接求值 | `age > 1 + 2` → `age > 3` |
| `constant_condition` | 恒真 / 恒假条件短路 | `1 = 1 AND age > 18` → `age > 18` |
| `not_elimination` | NOT 消除：比较取反 / 双重否定 / 德摩根律 | `NOT (age > 18)` → `age <= 18` |
| `filter_removal` | 谓词恒真时 Filter 算子整体消除 | `WHERE 1 = 1` → `Project(SeqScan)` |

- 每条规则命中都记录为 `[规则名] 说明`，经 `plan_with_optimization()` 返回；
- 改写**必须语义等价**，不得借优化之名改变查询结果；
- 当前不做谓词下推：本计划形状中 Filter 已紧贴 SeqScan，下推无实际意义。

## 依赖

- 依赖：`compiler/parser`（AST）、`compiler/semantic`（Catalog）
- 被依赖：`engine/executor`（执行计划）

## 代码规范

- 计划节点字段必须与 `docs/interface-contract.md` 第 1 节完全一致（engine 按此消费）
- 投影列的顺序严格按用户书写顺序，不做重排
- `to_dict()` 输出必须是纯 JSON 可序列化类型（无自定义对象嵌套）

## 禁止事项

- 禁止在计划中引用 AST 节点对象（必须是纯数据，便于序列化与跨模块传递）
- 禁止做物理优化（如选择索引、决定是否下推）——逻辑计划阶段不涉及物理信息
- 禁止任何**非等价**改写：优化后的计划必须与原始计划查询结果完全一致
- 禁止调用任何存储接口

## 完成标准

**P0 必做（已全绿）**

- [x] 四类语句均可生成对应计划
- [x] 树形 / JSON / S 表达式三种输出均可用且内容一致
- [x] `from_dict(to_dict(x)) == x` 往返无损
- [x] 不支持语法能给出 PlannerError

**P1 进阶（已实现）**

- [x] 4 条优化规则：常量折叠、恒真恒假简化、NOT 消除、Filter 消除
- [x] 优化前后计划可对比输出（`plan_with_optimization`）
- [x] 优化后计划仍可 JSON 往返（含 UnaryOp / 算术节点）
- [x] 测试 `tests/compiler/test_optimizer.py` 全绿

**P2 亮点（已实现）**

- [x] JOIN 计划：INNER / LEFT / CROSS，左深树，行键 `别名.列`
- [x] 聚合 / GROUP BY / HAVING 计划（AggregatePlan + Filter）
- [x] ORDER BY 计划（SortPlan，支持列 / 别名 / 序号 / 多键 / ASC-DESC）
- [x] 表达式投影（ProjectPlan.exprs + labels）
- [x] EXPLAIN 仅编译不执行（`explain_plan`，`plan=None`）
- [x] 图形化 Plan 可视化（`visualize.py`，内联 SVG + 自包含 HTML）
- [x] 新增算子全部支持 JSON 往返（`from_dict(to_dict(x)) == x`）
- [x] 优化器覆盖新算子（FunctionCall / Join / Aggregate / Sort / 2-arg Project）
- [x] 测试 `test_multitable.py`、`test_aggregate.py`、`test_explain_viz.py` 全绿

**P2（不做）**

- [ ] ~~代价模型与优化规则框架~~
- [ ] ~~JOIN 重排（join reorder）~~
- [ ] ~~物理计划选择（索引 / 谓词下推）~~

> **注**：JOIN / Aggregate / Sort 目前**只有编译器侧的计划生成与测试**，engine 尚未实现对应算子执行（按用户要求本轮不改引擎）。
> 因此对含这些算子的语句使用 `--execute` 仍会报"不支持的算子类型"；计划的正确性由编译器测试与 `tests/compiler/demo_advanced.py` 保证。
