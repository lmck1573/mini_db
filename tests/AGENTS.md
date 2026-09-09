# 测试规范

## 测试框架

Python 标准库 `unittest`（无需安装第三方依赖）。

```bash
# 全部测试
python -m unittest discover -s tests -t . -p "test_*.py" -v   # -t . 不可省略，否则 import src 失败

# 单个模块
python -m unittest tests.compiler.test_lexer -v             # 单个模块
```

## 范围与优先级

| 级别 | 测试范围 |
|------|---------|
| **P0 必做** | 基础测试：正确语句走通 + 错误语句定位，覆盖 compiler 四个阶段与 storage/engine 主流程 |
| **P1 进阶（暂缓）** | 隐藏测试高通过率（边界用例补全）、逻辑组合与复杂表达式用例 |
| **P2 扩展（不做）** | Fuzz Testing、随机压力测试框架 |

**P0 硬边界**：不为未实现的功能写测试；每条 P0 用例必须可一键跑通并全绿。

## 目录约定

```
tests/
├── compiler/     # 词法/语法/语义/计划 + SQL 样例文件(sql/)
├── storage/      # 页管理/缓存策略/磁盘持久化
└── engine/       # 建表→插入→查询→删除→再查询 的集成流程
```

## 命名规范

- 文件：`test_<模块名>.py`
- 类：`TestXxx`
- 方法：`test_<被测行为>_<预期结果>`，例如 `test_tokenize_string_with_unclosed_quote_raises`

## 测试数据

- 临时数据库文件一律建在 `tests/tmp/`（gitignore），测试结束清理
- SQL 样例放在 `tests/compiler/sql/`：`valid.sql`（正确语句）、`invalid.sql`（错误语句）

## 必须覆盖的用例

### compiler

- 词法：关键字/标识符/数字/字符串/运算符/分隔符识别；非法字符、未闭合字符串
- 语法：四类语句 AST；缺分号、缺 FROM、括号不匹配
- 语义：表不存在、列名拼错、类型不匹配、值个数不一致、重复建表
- 计划：树形/JSON/S 表达式输出；`from_dict(to_dict(x))` 往返一致

### storage

- 页分配/释放/复用；页读写往返
- LRU 与 FIFO 替换顺序差异；命中率统计
- 重启 DiskManager 后数据持久化

### engine

- 建表 → 插入 → 查询 → 条件查询 → 删除 → 再查询
- 重复建表、表不存在等错误路径
- 重启后数据持久性（1000 行量级）

## 完成标准

- [ ] 每个模块完成时有对应测试文件
- [ ] 错误路径必须有断言（不能只测 happy path）
- [ ] 集成测试可一键运行并全绿
