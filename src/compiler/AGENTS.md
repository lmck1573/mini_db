# compiler（SQL 编译器）模块开发规范

## 模块职责

把 SQL 文本翻译成**逻辑执行计划**：依次完成词法分析、语法分析、语义分析、计划生成。
本模块是纯前端，**只做翻译不做执行**，不感知磁盘、页、缓存或任何真实数据。

## 范围与优先级（P0 必做 / P1 进阶 / P2 扩展）

> P0 已全部完成；为个人亮点（评分标准"项目质量与创新性"）进一步实现了 P1 的表达式与规则优化，
> 以及 P2 中属于**编译器侧**的多表连接、聚合分组、排序、EXPLAIN 与计划可视化。

| 级别 | compiler 对应内容 |
|------|------------------|
| **P0 必做（已完成）** | Lexer+Token 位置、四类 SQL、AST、Catalog、语义检查（存在性/类型/列数）、Logical Plan、错误定位、基础测试 |
| **P1 进阶（已实现）** | AND/OR/NOT、复杂表达式（算术 + 嵌套括号）、规则优化（常量折叠 / 恒真消除 / NOT 消除 / Filter 消除） |
| **P2 编译器侧扩展（已实现）** | JOIN 及多表查询（INNER/LEFT/CROSS + 限定列 + 别名 + 列名歧义检查）、聚合 COUNT/SUM/AVG/MIN/MAX、GROUP BY / HAVING / ORDER BY、EXPLAIN、执行计划可视化（HTML/SVG） |
| **P2 非编译器侧（不做）** | Fuzz Testing、代价模型、规则优化框架、高级错误恢复（一次报多个错误）；事务/索引/缓存等属模块二、三职责 |

**当前边界**：
- SELECT 支持单表与多表（JOIN 链）；聚合函数与 GROUP BY 的组合受"分组一致性"约束校验；
- WHERE 支持任意逻辑、比较、算术组合的表达式树，但**不允许出现聚合函数**；
- 优化仅限**语义等价的改写**（不做算子树重排、不做谓词下推、不做代价估算）；
- 编译器**只产出计划，不执行**；执行由模块三引擎负责。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `str`（SQL 文本，可含多条以 `;` 分隔的语句） | 来自文件或标准输入 |
| 输出 | `list[StatementResult]` | 每条语句含 `tokens / ast / semantic_ok / plan / errors` |

统一出口：`SQLCompiler.compile(sql_text) -> list[StatementResult]`

## 目录与流水线

```
lexer/     词法：字符流 → Token 流
parser/    语法：Token 流 → AST
semantic/  语义：AST → 带注解 AST（维护 Catalog）
planner/   计划：AST → 逻辑执行计划
             planner.py    结构翻译，产出原始计划
             optimizer.py  规则优化，产出等价但更简洁的计划
             visualize.py  计划树 → 自包含 HTML（内嵌 SVG）
```

算子集合：`CreateTable / Insert / SeqScan / Filter / Project / Delete`
　　　　　`+ Join（多表连接） / Aggregate（分组聚合） / Sort（排序）`

## 接口定义

```python
class SQLCompiler:
    def __init__(self, catalog: Catalog | None = None): ...
    def compile(self, sql_text: str) -> list[StatementResult]: ...   # 遇错抛出 CompileError 子类
    def compile_safe(self, sql_text: str) -> tuple[list[StatementResult], CompileError | None]: ...
    # compile_safe 供 CLI 使用：出错时返回已成功部分 + 错误对象，便于打印部分结果

@dataclass
class StatementResult:
    sql: str
    tokens: list[Token]
    ast: Statement | None
    semantic_ok: bool
    plan: PlanNode | None         # 优化后的执行计划；EXPLAIN 语句为 None（不执行）
    raw_plan: PlanNode | None     # 优化前的原始计划（用于演示优化前后对比）
    optimizations: list[str]      # 命中的优化规则说明，未命中则为空列表
    explain: bool                 # 是否为 EXPLAIN
    explain_plan: PlanNode | None # EXPLAIN 的计划（只用于展示，不交给引擎）
    message: str                  # "语义检查通过" 等
    # 便捷属性：display_plan —— 普通语句返回 plan，EXPLAIN 返回 explain_plan
```

## 依赖

- 依赖：无（仅标准库）
- 被依赖：`engine/executor`（消费 PlanNode）

## 代码规范

- Token/AST/Plan 节点一律用 `@dataclass`，命名 `XxxNode` / `Xxx`
- 所有错误继承 `CompileError`，携带 `line` / `col`，格式化为 `[错误类型，位置，原因说明]`
- 各阶段目录内不得出现对其他两个模块的 import（见根 AGENTS.md 模块边界）
- 每个阶段模块需提供可独立调用的入口函数，便于单测：`tokenize()` / `parse()` / `analyze()` / `plan()`

## 禁止事项

- 禁止 import `src.storage` / `src.engine` 下任何内容
- 禁止在编译器中做文件 IO、读写数据库文件
- 禁止执行 SQL（不产生任何真实数据变更）

## 完成标准

**P0 必做（已全绿）**

- [x] 四类语句（CREATE TABLE / INSERT / SELECT / DELETE）走通全流程
- [x] Token 输出为 `[种别码，词素值，行号，列号]` 四元式
- [x] 语法错误含出错位置与期望符号
- [x] 语义错误含错误类型、位置、原因说明
- [x] 执行计划支持树形 / JSON / S 表达式三种输出

**P1 进阶（已实现，个人亮点①：复杂表达式 + 规则优化）**

- [x] AND / OR / NOT，括号可改变结合顺序
- [x] 算术表达式（+ - * /）与优先级、一元正负号
- [x] 语义层的表达式类型推导（数值 / 字符串 / 布尔），类型不匹配精确报错
- [x] 查询优化 4 条规则：常量折叠、恒真恒假简化、NOT 消除（含德摩根律）、Filter 消除
- [x] 优化前后计划可对比：`plan_with_optimization()` 返回 `(原始计划, 优化后计划, 命中规则)`

**P2 编译器侧扩展（已实现，个人亮点②：多表 / 聚合 / 可视化）**

- [x] JOIN 及多表查询：`JOIN … ON` / `INNER` / `LEFT` / `CROSS` / 逗号隐式连接、表别名、`a.id` 限定列
- [x] 多表语义检查：来源名重复、限定符不存在、列名歧义（提示用限定写法）、ON 必须引用两侧
- [x] 聚合函数 COUNT / SUM / AVG / MIN / MAX（含 `COUNT(*)`），GROUP BY / HAVING
- [x] ORDER BY（多键、ASC/DESC、投影别名、序号位置）
- [x] EXPLAIN：只编译输出计划，用 Catalog 副本干跑，不产生副作用、不执行
- [x] 计划可视化：`plan.to_html()` / `visualize.write_plan_html()` 输出自包含 HTML（内嵌 SVG 计划树）
- [x] CLI：`--plan-format html --plan-out plan.html`
- [x] tests/compiler 166 例全绿

**实现状态**：P0 + P1 + P2 编译器侧扩展已完成，CLI 可用 ——
`python -m src.main --file tests/compiler/sql/valid.sql`
`python -m src.main --file tests/compiler/sql/advanced.sql --plan-format tree`
`python tests/compiler/demo_advanced.py`（含可视化输出）

**依赖模块二/三的衔接说明**

- 新算子 `Join / Aggregate / Sort` 已进入计划 JSON，但**执行侧需由模块三实现对应算子**；
  在此之前 `--execute` 遇到这些算子会报"不支持的算子类型"，属预期行为（编译器侧已闭环可演示）。
- EXPLAIN 语句的 `plan` 为 `None`，引擎遍历结果时会自动跳过，不会误执行被解释的语句。

**仍不实现**

- [ ] ~~Fuzz Testing / 代价模型 / 规则优化框架 / 高级错误恢复~~
- [ ] ~~事务、索引（B+树）、缓存替换算法（属模块二/三职责）~~
