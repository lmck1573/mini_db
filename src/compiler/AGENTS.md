# compiler（SQL 编译器）模块开发规范

## 模块职责

把 SQL 文本翻译成**逻辑执行计划**：依次完成词法分析、语法分析、语义分析、计划生成。
本模块是纯前端，**只做翻译不做执行**，不感知磁盘、页、缓存或任何真实数据。

## 范围与优先级（P0 必做 / P1 进阶 / P2 扩展）

> 依据老师任务分级与 `docs/task-tiers.md`。**当前只做 P0，越界功能一律不实现。**

| 级别 | compiler 对应内容 |
|------|------------------|
| **P0 必做** | Lexer+Token 位置、四类 SQL、AST、Catalog、语义检查（存在性/类型/列数）、Logical Plan、错误定位、基础测试 |
| **P1 进阶（暂缓）** | AND/OR/NOT、复杂表达式（算术/嵌套括号）、谓词下推与常量折叠等优化、智能错误诊断、Plan 可视化 |
| **P2 扩展（不做）** | JOIN / GROUP BY / ORDER BY、Fuzz Testing、优化规则框架、代价模型、EXPLAIN、高级错误恢复 |

**P0 硬边界**：WHERE 只支持单条件 `列 比较符 值`；SELECT 只支持单表；不做任何优化；不实现未许可的语句类型。

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
```

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
    plan: PlanNode | None
    message: str            # "语义检查通过" 等
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

**P0 必做（本阶段必须全绿）**

- [x] 四类语句（CREATE TABLE / INSERT / SELECT / DELETE）走通全流程
- [x] Token 输出为 `[种别码，词素值，行号，列号]` 四元式
- [x] 语法错误含出错位置与期望符号
- [x] 语义错误含错误类型、位置、原因说明
- [x] 执行计划支持树形 / JSON / S 表达式三种输出
- [x] tests/compiler 下四类测试（正确语句 + 错误语句）全部通过（75 例全绿）

**实现状态**：P0 已完成，CLI 可用 —— `python -m src.main --file tests/compiler/sql/valid.sql`。

**P1/P2（本阶段禁止实现）**

- [ ] ~~AND/OR/NOT、复杂表达式~~
- [ ] ~~查询优化（谓词下推等）~~
- [ ] ~~JOIN / GROUP BY / EXPLAIN / 高级错误恢复~~
