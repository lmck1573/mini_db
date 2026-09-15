# semantic（语义分析器）开发规范

## 模块职责

在 AST 上做存在性检查、类型一致性检查、列数/列序检查，并构建维护模式目录（Catalog）。
通过后产出"带注解的 AST"（为计划生成补充类型等语义信息）。

**实现状态**：P0 + P1 + P2 亮点项已实现。文件 `catalog.py`（Catalog/TableSchema/Column + `to_dict/from_dict`）、`analyzer.py`（检查逻辑 + `_infer_type` 递归类型推导 + `Scope` 多表作用域）；测试 `tests/compiler/test_semantic.py`、`test_multitable.py`、`test_aggregate.py`。
注解字段：`Insert.resolved_columns`（解析后的目标列顺序）、`Select/Delete.resolved_types`（WHERE 推导出的类型，布尔条件为 `("BOOLEAN",)`）、`ColumnRef.resolved`（多表场景下补全限定列名，如 `s.id`，供 planner 生成无歧义的行键）。

## 范围与优先级

| 级别 | semantic 对应内容 |
|------|------------------|
| **P0 必做（已完成）** | 表/列存在性检查、类型一致性检查、列数/列序检查、Catalog 维护（增删改查 + 导出 dict）、错误三元组输出 |
| **P1 进阶（已实现）** | 逻辑 / 比较 / 算术表达式的递归类型推导（`_infer_type`），非法运算类型精确报错 |
| **P2 亮点（已实现）** | 多表（JOIN）语义检查、GROUP BY / 聚合函数语义校验、HAVING 与投影一致性检查、ORDER BY 合法性（别名 / 序号）检查 |
| **P2 扩展（不做）** | 高级错误恢复（一次报多个错误）、拼写纠错建议 |

**当前边界**：单表与多表（INNER/LEFT/CROSS JOIN）均支持；WHERE / HAVING / ORDER BY 支持任意表达式树，但 WHERE 顶层必须是布尔类型；
不产生纠错建议，只报 `[错误类型，位置，原因说明]`。

**多表作用域（`Scope`）**：由 FROM + 各 JOIN 的表（含别名）构建 alias → TableSchema 映射。列引用解析规则：
- 无限定 `col`：全作用域内唯一匹配才通过，多表命中即报 `列 'x' 有歧义`；
- 限定 `t.col`：按别名或表名精确定位，表不存在报错；
- 解析成功后写回 `ColumnRef.resolved`（实际行键，如 `s.id`），供 planner 生成无冲突的投影 / ON 条件。

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
| SELECT | 表存在；投影列名存在（`*` 跳过）；WHERE 整棵表达式递归检查（列存在 + 类型一致）；WHERE 顶层必须为 BOOLEAN |
| SELECT ... JOIN | JOIN 各表（含别名）存在；别名不得重复；INNER/LEFT JOIN 必须有 ON；ON 顶层必须是 BOOLEAN 且两侧列可解析；限定列前缀必须是已加入的别名 |
| SELECT ... GROUP BY / 聚合 | 聚合函数名限定为 COUNT/SUM/AVG/MIN/MAX；聚合实参必须是单列且非 `*`（`COUNT(*)` 除外）；聚合不得出现在 WHERE / GROUP BY / ON 中 |
| SELECT ... HAVING | HAVING 顶层必须为 BOOLEAN；HAVING 中出现的非聚合列必须出现在 GROUP BY 中 |
| SELECT ... ORDER BY | 排序列须可解析：列引用 / 聚合 / 投影别名 / 序号（`ORDER BY 2`）；序号须在投影列数范围内；无 GROUP BY 时不允许 ORDER BY 聚合 |
| DELETE | 表存在；WHERE 整棵表达式递归检查；WHERE 顶层必须为 BOOLEAN |

**分组一致性规则**：一旦出现聚合或 GROUP BY，SELECT 投影中所有非聚合列都必须包含在 GROUP BY 中，否则报 `列 'x' 必须出现在 GROUP BY 中`。

类型推导规则（`_infer_type` 递归，返回值取 INT / FLOAT / STRING / NULL / BOOLEAN）：

| 表达式 | 规则 | 结果类型 |
|--------|------|---------|
| 比较 `= <> != < <= > >=` | 两侧可比较 | BOOLEAN |
| 逻辑 `AND` / `OR` | 两侧均为 BOOLEAN | BOOLEAN |
| `NOT` | 操作数为 BOOLEAN | BOOLEAN |
| 算术 `+ - * /` | 两侧均为数值 | 含 FLOAT 则 FLOAT，否则 INT |
| 一元 `+` / `-` | 操作数为数值 | 同操作数 |
| 列引用 | 列必须存在 | INT / FLOAT / STRING |

类型兼容规则：

- `INT ↔ FLOAT` 兼容（隐式提升为 FLOAT）
- `INT/FLOAT ↔ VARCHAR/TEXT` 不兼容
- `NULL` 与任何类型兼容
- `BOOLEAN` 只能与 `BOOLEAN` 比较

## 依赖

- 依赖：`compiler/parser`（AST 节点）
- 被依赖：`compiler/planner`、`engine/catalog`（复用 Catalog 结构做持久化）

## 代码规范

- Catalog 中的表名/列名比较**大小写不敏感**（内部统一小写存储，保留原始拼写用于输出）
- 语义分析过程不得修改 AST 结构，只允许补充注解字段（如 `resolved_type`）
- 检查通过后由 analyzer 负责把 CREATE TABLE 的 schema 注册进 Catalog（顺序敏感：先建表后插入）

## 禁止事项

- 禁止读写磁盘（Catalog 持久化是 engine/catalog 的职责）
- 禁止在语义阶段改写用户 SQL 或"猜"列名（拼写纠错建议属 P2，当前不做）
- 禁止放过任何类型不匹配的赋值

## 完成标准

**P0 必做（已全绿）**

- [x] 表/列存在性、类型一致性、列数/列序检查全部实现
- [x] 错误格式为 `[错误类型，位置，原因说明]`
- [x] Catalog 可导出/导入 dict（便于与 engine 对接）
- [x] 覆盖测试：列名拼错、类型不匹配、值个数不一致、表不存在、重复建表

**P1 进阶（已实现）**

- [x] 表达式递归类型推导，嵌套表达式中的列错误也能定位到具体行列
- [x] 算术作用于字符串、逻辑作用于数值等误用能精确报错
- [x] WHERE 顶层非布尔表达式报错

**P2 亮点（已实现）**

- [x] 多表（JOIN）语义检查：别名唯一、ON 必填与布尔性、限定列前缀合法
- [x] 多表列歧义检测（无限定列在多张表中命中即报错）
- [x] GROUP BY 一致性：非聚合列必须出现在 GROUP BY 中
- [x] 聚合函数合法性（名称、实参、出现位置）
- [x] ORDER BY 合法性（列 / 聚合 / 别名 / 序号）
- [x] 测试 `tests/compiler/test_multitable.py`、`tests/compiler/test_aggregate.py` 全绿

**P2（不做）**

- [ ] ~~拼写纠错建议~~
- [ ] ~~多错误一次报告~~
