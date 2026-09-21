# semantic（语义分析器）开发规范

## 模块职责

在 AST 上做存在性检查、类型一致性检查、列数/列序检查，并构建维护模式目录（Catalog）。
通过后产出"带注解的 AST"（为计划生成补充类型等语义信息）。

**实现状态**：P0 已完成。文件 `catalog.py`（Catalog/TableSchema/Column + `to_dict/from_dict`）、`analyzer.py`（检查逻辑）；测试 `tests/compiler/test_semantic.py`。
注解字段：`Insert.resolved_columns`（解析后的目标列顺序）、`Select/Delete.resolved_types`（WHERE 两侧类型）。

## 范围与优先级

| 级别 | semantic 对应内容 |
|------|------------------|
| **P0 必做** | 表/列存在性检查、类型一致性检查、列数/列序检查、Catalog 维护（增删改查 + 导出 dict）、错误三元组输出 |
| **P1 进阶（暂缓）** | 智能错误诊断（列名拼错时给出"是否指 xxx"建议）、逻辑组合条件的类型推导 |
| **P2 扩展（不做）** | 多表（JOIN）语义检查、GROUP BY 语义校验、高级错误恢复（一次报多个错误） |

**P0 硬边界**：只处理单表、单条件；不产生纠错建议，只报 `[错误类型，位置，原因说明]`。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `list[Statement]` + `Catalog` | parser 输出 |
| 输出 | `list[Statement]`（原地注解）+ 检查结论 | 通过：`语义检查通过` |

错误输出格式（课程硬性要求）：

```
[错误类型，位置，原因说明]
```

示例：`[SemanticError, Line 1 Col 28, 表 'studnet' 不存在]`

## 接口定义

```python
@dataclass
class Column:
    name: str
    type: str          # INT / FLOAT / VARCHAR / TEXT
    length: int | None

@dataclass
class TableSchema:
    name: str
    columns: list[Column]

class Catalog:
    def create_table(self, schema: TableSchema) -> None: ...   # 重名抛 SemanticError
    def get_table(self, name: str) -> TableSchema | None: ...
    def has_table(self, name: str) -> bool: ...
    def get_column(self, table: str, col: str) -> Column | None: ...
    def to_dict(self) -> dict: ...                              # 供 engine 持久化
    @staticmethod
    def from_dict(d: dict) -> "Catalog": ...

def analyze(statements: list[Statement], catalog: Catalog) -> None: ...  # 抛 SemanticError
```

## 检查项

| 语句 | 检查内容 |
|------|---------|
| CREATE TABLE | 表名重复；列名重复；类型合法；VARCHAR 长度为正整数 |
| INSERT | 表存在；显式列名均存在且无重复；值个数 == 列个数；值类型与列类型兼容；列序按给定列名映射 |
| SELECT | 表存在；投影列名存在（`*` 跳过）；WHERE 中列引用存在；比较两侧类型兼容 |
| DELETE | 表存在；WHERE 中列引用存在；比较两侧类型兼容 |

类型兼容规则：

- `INT ↔ FLOAT` 兼容（隐式提升为 FLOAT）
- `INT/FLOAT ↔ VARCHAR/TEXT` 不兼容
- `NULL` 与任何类型兼容

## 依赖

- 依赖：`compiler/parser`（AST 节点）
- 被依赖：`compiler/planner`、`engine/catalog`（复用 Catalog 结构做持久化）

## 代码规范

- Catalog 中的表名/列名比较**大小写不敏感**（内部统一小写存储，保留原始拼写用于输出）
- 语义分析过程不得修改 AST 结构，只允许补充注解字段（如 `resolved_type`）
- 检查通过后由 analyzer 负责把 CREATE TABLE 的 schema 注册进 Catalog（顺序敏感：先建表后插入）

## 禁止事项

- 禁止读写磁盘（Catalog 持久化是 engine/catalog 的职责）
- 禁止在语义阶段改写用户 SQL 或"猜"列名（纠错建议属 P1，当前不做）
- 禁止放过任何类型不匹配的赋值

## 完成标准

**P0 必做**

- [x] 表/列存在性、类型一致性、列数/列序检查全部实现
- [x] 错误格式为 `[错误类型，位置，原因说明]`
- [x] Catalog 可导出/导入 dict（便于与 engine 对接）
- [x] 覆盖测试：列名拼错、类型不匹配、值个数不一致、表不存在、重复建表

**P1/P2（本阶段禁止实现）**

- [ ] ~~拼写纠错建议~~
- [ ] ~~多表语义检查 / GROUP BY 校验~~
- [ ] ~~多错误一次报告~~
