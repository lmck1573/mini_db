# parser（语法分析器）开发规范

## 模块职责

基于递归下降方法，将 Token 流构造成抽象语法树（AST），并在语法错误时给出出错位置与期望符号。
不做语义判断（不检查表/列是否存在）。

**实现状态**：P0 已完成。文件 `ast_nodes.py`（AST 节点 + `to_tree()`）、`parser.py`（递归下降）；测试 `tests/compiler/test_parser.py`。

## 范围与优先级

| 级别 | parser 对应内容 |
|------|----------------|
| **P0 必做** | 四类语句 AST、`CREATE TABLE` 列定义与类型、`INSERT` 单/多值、单表 `SELECT`/`DELETE`、**单条件** WHERE（比较表达式）、缺分号/缺关键字等语法错误定位 |
| **P1 进阶（暂缓）** | AND / OR / NOT 逻辑组合、复杂表达式（算术 `+ - * /`、嵌套括号、优先级） |
| **P2 扩展（不做）** | JOIN / GROUP BY / ORDER BY / 子查询语法、高级错误恢复（一次报多个错误） |

**P0 硬边界**：

- WHERE 条件只允许 `列 比较符 值`（比较符 `= <> != < <= > >=`），两侧为列引用或字面量
- 不实现 `or_expr / and_expr / not_expr / additive / multiplicative / unary` 这几层（下方文法中标注为 P1）
- 遇到 AND/OR/NOT 或算术运算符时，按**语法错误**报出（提示当前版本不支持），而不是静默解析

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `list[Token]` | lexer 输出（含 EOF） |
| 输出 | `list[Statement]` | 每条 SQL 一个语句节点 |

## 接口定义

```python
def parse(tokens: list[Token]) -> list[Statement]: ...   # 抛 SyntaxErr

# AST 节点（dataclass，均带 line/col）
CreateTable(table_name, columns: list[ColumnDef])
ColumnDef(name, type: str, length: int | None)
Insert(table_name, columns: list[str] | None, rows: list[list[Literal]], resolved_columns=None)
Select(table_name, star: bool, columns: list[ColumnRef], where: BinaryOp | None, resolved_types=None)
Delete(table_name, where: BinaryOp | None, resolved_types=None)

# 表达式节点（P0 只用前三个）
Literal(value, value_type)      # value_type: INT/FLOAT/STRING/NULL
ColumnRef(name)
BinaryOp(op, left, right)       # P0：仅用于比较条件，op ∈ = <> != < <= > >=
# UnaryOp(op, operand)          # P1 进阶：暂不实现
```

## SQL 子集文法（EBNF）

```ebnf
(* ===== P0 必做：本阶段实现以下部分 ===== *)
statements   := statement (';' statement)* ';'
statement    := create_table | insert | select | delete
create_table := 'CREATE' 'TABLE' IDENT '(' col_def (',' col_def)* ')'
col_def      := IDENT data_type
data_type    := 'INT' | 'FLOAT' | 'TEXT' | 'VARCHAR' [ '(' INT ')' ]
insert       := 'INSERT' 'INTO' IDENT [ '(' IDENT (',' IDENT)* ')' ]
               'VALUES' value_tuple (',' value_tuple)*
value_tuple  := '(' literal (',' literal)* ')'
select       := 'SELECT' ('*' | column (',' column)*) 'FROM' IDENT [ 'WHERE' condition ]
delete       := 'DELETE' 'FROM' IDENT [ 'WHERE' condition ]

(* 单条件：列 比较符 值 *)
condition    := operand ('='|'<>'|'!='|'<'|'<='|'>'|'>=') operand
operand      := IDENT | CONST

(* ===== P1 进阶：本阶段明确不实现 ===== *)
(* expr         := or_expr
   or_expr      := and_expr ('OR' and_expr)*
   and_expr     := not_expr ('AND' not_expr)*
   not_expr     := 'NOT' not_expr | comparison
   comparison   := additive [ ('='|'<>'|'!='|'<'|'<='|'>'|'>=') additive ]
   additive     := multiplicative (('+'|'-') multiplicative)*
   multiplicative := unary (('*'|'/') unary)*
   unary        := ('-'|'+') unary | primary
   primary      := CONST | IDENT | '(' expr ')' *)
```

> 说明：P1 部分以注释形式保留，仅为后续扩展留痕，**当前不得实现**。
> SELECT 列表中出现 `*`（非 SELECT \*）时同样按语法错误处理。

## 依赖

- 依赖：`compiler/lexer`（Token / TokenType）
- 被依赖：`compiler/semantic`

## 代码规范

- 递归下降：一个文法符号一个方法（`_parse_select` / `_parse_expr` ...）
- 期望符号不匹配时抛 `SyntaxErr(expected, got_token)`，错误信息形如
  `[SyntaxError, Line 1 Col 20, 期望 FROM，实际得到 IDENTIFIER('student')]`
- `'*'` 在 SELECT 列表中特判为全列投影，不进入 expr 解析

## 禁止事项

- 禁止在语法阶段访问 Catalog 或做任何存在性检查
- 禁止使用正则/字符串切分代替 Token 流解析
- 禁止吞掉错误继续解析（遇到第一个语法错误即抛出）

## 完成标准

**P0 必做**

- [x] 四类语句均可生成正确 AST
- [x] 支持多值 INSERT：`VALUES (...),(...)`
- [x] 支持 6 种比较运算符的单条件 WHERE
- [x] 缺分号、缺关键字、括号不匹配等错误能报出位置与期望符号
- [x] 单测覆盖 4 条正确语句 + 4 类语法错误（缺分号、缺 FROM、括号不匹配、列名位置非法）

**P1/P2（本阶段禁止实现）**

- [ ] ~~AND / OR / NOT 逻辑组合~~
- [ ] ~~算术表达式与嵌套括号~~
- [ ] ~~JOIN / GROUP BY / ORDER BY 语法~~
