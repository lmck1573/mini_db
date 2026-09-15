# parser（语法分析器）开发规范

## 模块职责

基于递归下降方法，将 Token 流构造成抽象语法树（AST），并在语法错误时给出出错位置与期望符号。
不做语义判断（不检查表/列是否存在）。

**实现状态**：P0 + P1 表达式已实现。文件 `ast_nodes.py`（AST 节点 + `to_tree()`）、`parser.py`（递归下降，分层表达式解析）；测试 `tests/compiler/test_parser.py`。

## 范围与优先级

| 级别 | parser 对应内容 |
|------|----------------|
| **P0 必做（已完成）** | 四类语句 AST、`CREATE TABLE` 列定义与类型、`INSERT` 单/多值、单表 `SELECT`/`DELETE`、缺分号/缺关键字等语法错误定位 |
| **P1 进阶（已实现）** | AND / OR / NOT 逻辑组合、复杂表达式（算术 `+ - * /`、嵌套括号、优先级、一元正负号） |
| **P2 编译器侧扩展（已实现）** | `JOIN … ON` / `INNER` / `LEFT` / `CROSS` / 逗号隐式连接、表别名（`AS` 或裸写）、`a.id` 限定列、聚合函数调用 `COUNT/SUM/AVG/MIN/MAX`（含 `COUNT(*)`）、`GROUP BY` / `HAVING` / `ORDER BY`（ASC/DESC）、投影别名、`EXPLAIN` |
| **P2 非编译器侧（不做）** | 子查询、UNION、RIGHT/FULL JOIN、高级错误恢复（一次报多个错误） |

**语法层边界**：

- `t.*` 形式不支持（直接写 `*`）；`RIGHT/FULL JOIN` 明确报错；
- 函数名合法性（是否聚合函数、参数个数/类型）不在语法层判断，交由语义层校验；
- 聚合函数在语法层与普通函数调用同形，因此 `FOO(x)` 能通过语法分析、由语义层报"不支持的函数"。

**当前边界**：

- WHERE 支持**完整表达式树**：逻辑（AND / OR / NOT）、比较（`= <> != < <= > >=`）、算术（`+ - * /`）、嵌套括号
- SELECT 列表仍只支持 `*` 或列名（不支持 `SELECT age + 1`）
- INSERT 的值仍只允许常量或 NULL（不支持表达式）
- 优先级（自低到高）：`OR < AND < NOT < 比较 < 加减 < 乘除 < 一元 < 括号 / 原子`

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
Select(table_name, star: bool, columns: list[ColumnRef], where: Expr | None, resolved_types=None)
Delete(table_name, where: Expr | None, resolved_types=None)

# 表达式节点（Expr = Literal | ColumnRef | BinaryOp | UnaryOp）
Literal(value, value_type)      # value_type: INT / FLOAT / STRING / NULL / BOOLEAN
ColumnRef(name)
BinaryOp(op, left, right)       # op ∈ 比较(= <> != < <= > >=) | 逻辑(AND/OR) | 算术(+ - * /)
UnaryOp(op, operand)            # op ∈ NOT / + / -
```

## SQL 子集文法（EBNF）

```ebnf
statements   := statement (';' statement)* ';'
statement    := create_table | insert | select | delete | explain
explain      := 'EXPLAIN' statement
create_table := 'CREATE' 'TABLE' IDENT '(' col_def (',' col_def)* ')'
col_def      := IDENT data_type
data_type    := 'INT' | 'FLOAT' | 'TEXT' | 'VARCHAR' [ '(' INT ')' ]
insert       := 'INSERT' 'INTO' IDENT [ '(' IDENT (',' IDENT)* ')' ]
               'VALUES' value_tuple (',' value_tuple)*
value_tuple  := '(' literal (',' literal)* ')'

select       := 'SELECT' ('*' | select_item (',' select_item)*)
                'FROM' table_ref { join_clause }
                [ 'WHERE' expr ]
                [ 'GROUP' 'BY' expr (',' expr)* ]
                [ 'HAVING' expr ]
                [ 'ORDER' 'BY' order_item (',' order_item)* ]
select_item  := expr [ 'AS' IDENT | IDENT ]        (* 裸写别名亦可 *)
order_item   := expr [ 'ASC' | 'DESC' ]
table_ref    := IDENT [ 'AS' IDENT | IDENT ]       (* 表别名 *)
join_clause  := [ 'INNER' ] 'JOIN' table_ref 'ON' expr
              | 'LEFT' [ 'OUTER' ] 'JOIN' table_ref 'ON' expr
              | 'CROSS' 'JOIN' table_ref
              | ',' table_ref                       (* 逗号隐式连接 ≡ CROSS JOIN *)
delete       := 'DELETE' 'FROM' IDENT [ 'WHERE' expr ]

(* 表达式：按优先级自低到高，一层一个方法 *)
expr           := or_expr
or_expr        := and_expr ('OR' and_expr)*
and_expr       := not_expr ('AND' not_expr)*
not_expr       := 'NOT' not_expr | comparison
comparison     := additive [ ('='|'<>'|'!='|'<'|'<='|'>'|'>=') additive ]
additive       := multiplicative (('+'|'-') multiplicative)*
multiplicative := unary (('*'|'/') unary)*
unary          := ('-'|'+') unary | primary
primary        := literal | column | function_call | '(' expr ')'
column         := IDENT [ '.' IDENT ]               (* 限定列 a.id *)
function_call  := IDENT '(' ( expr | '*' ) ')'      (* COUNT(*) / SUM(age) *)
literal        := CONST | 'NULL'
```

> 说明：`primary` 的括号内按完整 `expr` 解析，因此 `(a > 1 AND b < 2)`
> 与 `(age + 1) * 2 > 40` 两种写法都支持。
> SELECT 列表中的 `*` 单独特判为全列投影，不进入 expr 解析；
> `t.*` 形式明确报错（提示直接写 `*`）。

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

**P0 必做（已全绿）**

- [x] 四类语句均可生成正确 AST
- [x] 支持多值 INSERT：`VALUES (...),(...)`
- [x] 支持 6 种比较运算符
- [x] 缺分号、缺关键字、括号不匹配等错误能报出位置与期望符号

**P1 进阶（已实现）**

- [x] AND / OR / NOT 逻辑组合
- [x] 算术表达式（`+ - * /`）与优先级、一元正负号
- [x] 嵌套括号改变结合顺序
- [x] `UnaryOp` 节点与 `Expr` 联合类型
- [x] 单测覆盖复杂嵌套表达式（如 `(age + 1) * 2 > 40 AND name <> 'x'`）

**P2 编译器侧扩展（已实现）**

- [x] 限定列 `a.id`（`ColumnRef.table`）与表别名（`TableRef.alias`）
- [x] `JOIN … ON` / `INNER JOIN` / `LEFT [OUTER] JOIN` / `CROSS JOIN` / 逗号隐式连接
- [x] 聚合函数调用 `COUNT(*)` / `SUM(x)` / `AVG` / `MIN` / `MAX`（`FunctionCall` 节点）
- [x] `GROUP BY` / `HAVING` / `ORDER BY`（多键、ASC/DESC）
- [x] 投影别名（`AS x` 与裸写）与 `ORDER BY` 序号
- [x] `EXPLAIN` 前缀（`Explain` 节点），不支持嵌套
- [x] 单测：`tests/compiler/test_multitable.py`、`test_aggregate.py`

**仍不实现**

- [ ] ~~子查询 / UNION / RIGHT JOIN / FULL JOIN~~
- [ ] ~~高级错误恢复（一次报多个错误）~~
