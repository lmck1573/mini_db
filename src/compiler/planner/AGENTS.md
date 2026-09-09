# planner（执行计划生成器）开发规范

## 模块职责

把语义分析通过的 AST 翻译为**逻辑执行计划**（Plan 树），是编译器的最后一道工序（类比"目标代码生成"）。
只做结构翻译，**不做任何优化**（谓词下推、常量折叠等属 P1/P2，当前禁止实现）。

**实现状态**：P0 已完成。文件 `plan_nodes.py`（6 类算子 + 三种输出 + `from_dict`）、`planner.py`（AST → Plan）；测试 `tests/compiler/test_planner.py`。

## 范围与优先级

| 级别 | planner 对应内容 |
|------|-----------------|
| **P0 必做** | 四类语句 → 标准形状计划（CreateTable / Insert / SeqScan+Filter / Project / Delete）；树形、JSON、S 表达式三种文本输出；`from_dict(to_dict())` 往返无损 |
| **P1 进阶（暂缓）** | ≥2 条优化规则（谓词下推、常量折叠、列裁剪）、Plan 图形化可视化 |
| **P2 扩展（不做）** | 规则优化框架、简单代价模型、EXPLAIN |

**P0 硬边界**：计划形状固定（Project → Filter → SeqScan），不做重排、不下推、不合并；不涉及物理信息（无索引、无代价）。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `Statement` + `Catalog` | 语义分析后的 AST |
| 输出 | `PlanNode` | 逻辑执行计划树 |

计划必须支持三种输出形式（课程要求任选，本模块全部提供）：

1. 树形结构（`to_tree()`）
2. JSON（`to_json()` / `to_dict()`）
3. S 表达式（`to_sexpr()`）

## 接口定义

```python
class PlanNode:                       # 基类
    def to_dict(self) -> dict: ...
    def to_json(self, indent=2) -> str: ...
    def to_tree(self, prefix="") -> str: ...
    def to_sexpr(self) -> str: ...
    @staticmethod
    def from_dict(d: dict) -> "PlanNode": ...

CreateTablePlan(table_name, columns: list[ColumnDef])
InsertPlan(table_name, columns: list[str] | None, rows: list[list[Literal]])
SeqScanPlan(table_name)
FilterPlan(predicate: Expr, child: PlanNode)
ProjectPlan(columns: list[str] | "*", child: PlanNode)
DeletePlan(table_name, predicate: Expr | None)

def plan(statement: Statement, catalog: Catalog) -> PlanNode: ...  # 抛 PlannerError
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

不支持的语法或缺失语义信息时抛 `PlannerError`，格式 `[PlannerError, Line x Col y, 原因说明]`。

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
- 禁止调用任何存储接口

## 完成标准

**P0 必做**

- [x] 四类语句均可生成对应计划
- [x] 树形 / JSON / S 表达式三种输出均可用且内容一致
- [x] `from_dict(to_dict(x)) == x` 往返无损
- [x] 不支持语法能给出 PlannerError

**P1/P2（本阶段禁止实现）**

- [ ] ~~查询优化（谓词下推 / 常量折叠 / 列裁剪）~~
- [ ] ~~优化规则框架与代价模型~~
- [ ] ~~EXPLAIN / 图形化 Plan 可视化~~
