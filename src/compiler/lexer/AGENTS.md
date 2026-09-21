# lexer（词法分析器）开发规范

## 模块职责

把 SQL 字符流切分为有序 Token 序列，并在遇到非法字符时给出带位置的错误提示。
不做任何语法判断（不关心 Token 顺序是否合法）。

**实现状态**：P0 已完成。文件 `token.py`（Token/TokenType/ConstType）、`keywords.py`（关键字表）、`lexer.py`（Lexer/tokenize）；测试 `tests/compiler/test_lexer.py`（24 例全绿）。

## 范围与优先级

| 级别 | lexer 对应内容 |
|------|---------------|
| **P0 必做** | 关键字/标识符/常量/运算符/分隔符识别、位置信息（行号列号）、非法字符与未闭合字符串报错 |
| **P1 进阶（暂缓）** | 错误信息中的"是否想输入 XX"智能纠错提示 |
| **P2 扩展（不做）** | 高级错误恢复（恐慌模式、一次报告多个错误）、Fuzz 输入鲁棒性 |

**P0 硬边界**：只做切词与定位；不判断语句结构（缺分号属语法错误），不做纠错建议。
运算符需识别 `+ - * /` 等字符，但运算符的**语义使用**（算术表达式）不在 P0 范围，由 parser 侧限制。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `str` | 原始 SQL 文本 |
| 输出 | `list[Token]` | 末尾含 EOF Token |

Token 的规范输出形式为四元式：

```
[种别码，词素值，行号，列号]
```

示例：`[KEYWORD, SELECT, 1, 1]`、`[IDENTIFIER, student, 1, 15]`、`[CONST, 20, 1, 42]`

## 接口定义

```python
class TokenType(Enum):
    KEYWORD / IDENTIFIER / CONST / OPERATOR / DELIMITER / EOF

@dataclass
class Token:
    type: TokenType
    lexeme: str
    line: int          # 从 1 开始
    col: int           # 从 1 开始
    value_type: str | None = None   # CONST 专用：INT / FLOAT / STRING / NULL
    value: Any | None = None        # CONST 专用：解析后的字面值

def tokenize(sql: str) -> list[Token]: ...   # 抛 LexicalError
```

## 词法规则

| 类别 | 规则 | 示例 |
|------|------|------|
| 关键字 | 见关键字表，大小写不敏感，内部统一存大写 | `SELECT FROM WHERE CREATE TABLE INSERT INTO VALUES DELETE AND OR NOT NULL INT FLOAT VARCHAR TEXT` |
| 标识符 | `[A-Za-z_][A-Za-z0-9_]*`，非关键字 | `student`、`id` |
| 整数常量 | 数字串，可带负号 | `20`、`-5` |
| 浮点常量 | `digits . digits`，可带负号 | `3.14`、`-0.5` |
| 字符串常量 | 单引号包裹，支持 `''` 转义 | `'Alice'` |
| 运算符 | `= <> != < <= > >= + - * /` | |
| 分隔符 | `( ) , ;` | |
| 空白/注释 | 空格、制表、换行；`--` 行注释、`/* */` 块注释 | |

## 依赖

- 依赖：无
- 被依赖：`compiler/parser`

## 代码规范

- 关键字集中放在 `keywords.py`，禁止硬编码散落
- 位置信息（line/col）必须在扫描时同步维护，禁止事后补算
- 遇到非法字符立即抛 `LexicalError(line, col, reason)`，**不做容错跳过**（课程要求定位精确）
- **负数字面量规则**：`-` 紧跟数字、且前一个 Token 不是 CONST / IDENTIFIER / `)` 时，负号折进数字常量产出单个 CONST（`-5` → `[CONST, -5, ...]`）；其他情况 `-` 仍为运算符（算术属 P1，由 parser 报错）。此规则只为让负常量可用，**不引入一元运算符**

## 禁止事项

- 禁止在词法阶段判断语句是否合法（例如"缺分号"属于语法错误，不是词法错误）
- 禁止把关键字识别为标识符

## 完成标准

**P0 必做**

- [x] 正确识别关键字、标识符、数字/浮点/字符串常量、运算符、分隔符
- [x] 输出四元式 `[种别码，词素值，行号，列号]`（`Token.to_tuple()`）
- [x] 非法字符、未闭合字符串能报出 `[LexicalError, Line x Col y, 原因]`
- [x] 测试用例覆盖正常语句与错误语句（非法字符、未闭合字符串、非法符号）

**P1/P2（本阶段禁止实现）**

- [ ] ~~智能纠错建议~~
- [ ] ~~恐慌模式错误恢复 / 多错误一次报告~~
