# Mini-DB

课程实训《大型平台软件设计实习》项目：**从零分步构建一个简化数据库系统**。

Mini-DB 贯通三门课程的知识，实现了一条完整的 SQL 执行链路：

```
SQL 文本
  → [compiler] Token 流 → AST → 语义检查 → 逻辑执行计划(Plan)
  → [engine]   执行计划 → 算子(CreateTable/Insert/SeqScan/Filter/Project/Delete)
  → [storage]  页级读写(get_page/write_page) + 缓存(LRU/FIFO) + 磁盘文件
```

## 功能特性

- ✅ **SQL 编译器**：词法分析 → 语法分析 → 语义分析 → 执行计划生成（四阶段全量可视化输出）
- ✅ **页式存储系统**：4KB 页、空闲页复用、LRU/FIFO 缓存替换、脏页落盘、宕机重启恢复
- ✅ **数据库执行引擎**：火山模型算子、表堆存储、系统目录持久化、端到端 CRUD
- ✅ **交互式 CLI**：编译器模式与数据库执行模式，支持 Token/AST/Plan 三种形式输出
- ✅ **零第三方依赖**：仅用 Python 标准库，测试基于 `unittest`

## 技术栈

**Python 3.11+**（推荐 3.13），无第三方运行时依赖。

选择 Python 的原因：

- 开发效率高，编译器的 Token/AST/Plan 这类树形结构用 dataclass 表达最简洁；
- 标准库 `collections.OrderedDict` 天然适合实现 LRU 缓存（对应操作系统模块的缓存管理要求）；
- `struct` / `bytes` 便于实现行序列化与页式存储模拟；
- 调试方便，交互式 CLI 便于演示「Token 流 → AST → 语义检查 → 执行计划」的完整链路。

## 目录结构

```
mini-db/
├── AGENTS.md                    # 项目总规范（模块边界、开发规则、分支策略）
├── README.md
├── .gitignore
├── run.bat / run.sh             # 一键运行脚本（Windows / Unix）
├── test.bat / test.sh           # 一键测试脚本
├── demo.sql                     # 端到端演示 SQL
├── auto_validate.py             # 页式存储系统全自动暴力测试
├── export_sqlite.py             # 将 Mini-DB 数据导出为标准 SQLite 文件
├── requirements.txt             # 占位（当前无第三方依赖）
├── docs/
│   ├── architecture.md          # 总体架构说明
│   ├── interface-contract.md    # 模块间接口契约（唯一事实来源）
│   └── task-tiers.md            # 任务分级清单（P0/P1/P2）
├── src/
│   ├── compiler/                # 模块一：SQL 编译器
│   │   ├── lexer/               #   词法分析
│   │   ├── parser/              #   语法分析
│   │   ├── semantic/            #   语义分析 + Catalog
│   │   └── planner/             #   执行计划生成
│   ├── storage/                 # 模块二：页式存储系统
│   │   ├── page/                #   页管理器
│   │   ├── cache/               #   缓存与替换策略
│   │   ├── disk/                #   磁盘文件读写
│   │   └── storage_manager.py   #   对外统一入口 StorageManager
│   ├── engine/                  # 模块三：数据库系统
│   │   ├── executor/            #   执行算子
│   │   ├── catalog/             #   系统目录（持久化）
│   │   ├── storage_engine/      #   存储引擎（行/页映射）
│   │   ├── expr.py              #   谓词求值器
│   │   └── database.py          #   Database 门面（CLI 调用的入口）
│   └── main.py                  # CLI 入口
├── tests/                       # 单元测试 + 集成回归（test_merge3.py）
└── data/                        # 运行时数据目录（数据库文件 mini.db）
```

## 快速开始

> 以下命令需在项目根目录（含 `src/` 的目录）执行。

```bash
# 编译模式：输出 Token 流 / AST / 语义检查 / 执行计划
python -m src.main --file tests/compiler/sql/valid.sql

# 交互式编译（输入 SQL，以 ; 结束；输入 exit 退出）
python -m src.main

# 只输出某一种形式的执行计划（tree / json / sexpr / all）
python -m src.main --file demo.sql --plan-format json

# 数据库执行模式：实际执行 SQL 并打印结果集
python -m src.main --execute
python -m src.main --execute --file demo.sql

# 输出 storage 缓存 [_CACHE] 日志（默认关闭）
python -m src.main --execute --file demo.sql --verbose

# 运行全部测试（-t . 不可省略）
python -m unittest discover -s tests -t . -p "test_*.py" -v
```

### Windows 一键运行（推荐）

Windows 上 `python` 可能是 Microsoft Store 的占位程序（直接运行会报错），且终端默认 GBK 编码会让中文输出乱码。项目内置启动脚本自动处理这两点（切换 UTF-8、优先使用 `.venv` 解释器）：

```bat
:: 运行编译器（参数原样透传给 python -m src.main）
run.bat --file tests\compiler\sql\valid.sql
run.bat                            :: 交互模式
run.bat --execute --file demo.sql  :: 数据库执行模式

:: 运行全部测试
test.bat
```

命令行（cmd / PowerShell）直接双击或输入脚本名即可；Git Bash 下使用 `./run.sh`、`./test.sh`。

首次使用先创建虚拟环境（一次性）：

```bat
py -m venv .venv
```

（无第三方依赖，无需 `pip install`；`requirements.txt` 仅作占位。）

## 支持的 SQL 语法（P0 范围）

| 语句 | 语法 | 说明 |
|------|------|------|
| 建表 | `CREATE TABLE t (id INT, name VARCHAR(32), score FLOAT);` | 类型：`INT` / `FLOAT` / `VARCHAR(n)` / `TEXT` |
| 插入 | `INSERT INTO t VALUES (1, 'Alice', 90.5), (2, 'Bob', 80.0);` | 支持一次多值、显式列名 |
| 查询 | `SELECT id, name FROM t WHERE score > 82.0;` | `*` / 指定列；单条件 WHERE |
| 删除 | `DELETE FROM t WHERE id = 2;` | 带或不带 WHERE（不带 = 清空表） |

比较符：`=  <>  !=  <  <=  >  >=`

**范围约束**（依据 `docs/task-tiers.md`，当前只实现 P0 必做项）：

- WHERE 仅支持单条件 `列 比较符 值`，**不支持** AND/OR/NOT、算术表达式、嵌套括号；
- SELECT 仅单表，**不支持** JOIN / 子查询；
- **不做任何查询优化**，计划形状固定为 `Project → Filter → SeqScan`；
- `UPDATE`、`ORDER BY`、`GROUP BY`、`EXPLAIN` 等一律报 `SyntaxError` 而非静默解析。

## 架构概览

三个模块各自独立、并行开发，`engine` 是唯一同时了解两侧的汇合点：

```
┌─────────────────────────────────────────────────────────┐
│  CLI / 交互层 (src/main.py)                              │
└─────────────────────────────────────────────────────────┘
                    │                      ▲
                    ▼                      │ 结果集 / 错误信息
┌─────────────────────────────────────────────────────────┐
│  模块三：engine/ 数据库系统                              │
│  executor(执行算子)  catalog(系统目录)  storage_engine    │
└─────────────────────────────────────────────────────────┘
        ▲ 逻辑执行计划(Plan)          │ 页级读写
        │                              ▼
┌───────────────┐      ┌──────────────────────────────────┐
│ 模块一：       │      │ 模块二：storage/ 页式存储系统     │
│ compiler/     │      │ cache → page → disk (LRU/FIFO)   │
└───────────────┘      └──────────────────────────────────┘
                                                    │
                                                    ▼
                                            data/mini.db (4KB 页)
```

- **模块一 compiler/**：四阶段流水线（词法 → 语法 → 语义 → 计划），只负责「把 SQL 翻译成计划」，不感知磁盘、页、缓存。
- **模块二 storage/**：按 4KB 页存字节，提供 `get_page / write_page / alloc_page` 与 LRU/FIFO 缓存，不感知 SQL、表、行语义。
- **模块三 engine/**：火山模型算子，把计划翻译成页级访问，含系统目录持久化与重启恢复。

详细设计见 [`docs/architecture.md`](docs/architecture.md)，模块间接口见 [`docs/interface-contract.md`](docs/interface-contract.md)。

## 测试

```bash
# 全部 unittest 用例（含 compiler / storage / engine 单测与跨模块集成回归）
python -m unittest discover -s tests -t . -p "test_*.py" -v
test.bat          # Windows
./test.sh         # Git Bash / macOS / Linux

# 页式存储系统全自动暴力测试（宕机重启 / LRU 脏页 / 空闲页复用 / 非 4KB 拒绝）
python auto_validate.py
```

`tests/test_merge3.py` 是跨三模块的集成回归，覆盖单测未覆盖的空白：compiler 四阶段一次联产、共享谓词求值器（7 个比较符 / NULL 三值逻辑 / INT-FLOAT 提升）、经 `Database` 门面的端到端 CRUD、多表重启后的数据与目录恢复、编译错误传播为失败结果、storage 空闲链表与魔数校验、`[_CACHE]` 日志的 verbose 开关。

## 工具脚本

| 脚本 | 用途 |
|------|------|
| `run.bat` / `run.sh` | 一键运行（自动处理 UTF-8 编码与解释器选择） |
| `test.bat` / `test.sh` | 一键运行全部 unittest |
| `auto_validate.py` | 页式存储系统全自动暴力测试，通过只打印 `🎉 ALL TESTS PASSED` |
| `export_sqlite.py` | 将 `data/mini.db` 的表结构 + 数据导出为标准 SQLite 文件，供 Navicat 等工具查看 |

```bash
# 把 Mini-DB 数据导出为 SQLite（Navicat 可直接打开）
python export_sqlite.py                    # data/mini.db -> mini_db_export.db
python export_sqlite.py --out 学生库.db     # 自定义输出文件名
```

## 任务分级（当前只做 P0）

完整清单见 [`docs/task-tiers.md`](docs/task-tiers.md)。

| 级别 | 内容 | 状态 |
|------|------|------|
| **P0 必做** | Lexer + Token 位置、四类 SQL、AST、Catalog、语义检查、Logical Plan、错误定位、基础测试 | ✅ 已实现 |
| **P1 进阶** | AND/OR/NOT、复杂表达式、≥2 条优化规则、智能错误诊断、Plan 可视化、隐藏测试通过率 | ⏸ 暂缓 |
| **P2 扩展** | JOIN/GROUP BY、Fuzz Testing、优化框架、代价模型、EXPLAIN、高级错误恢复 | 🚫 不做 |

**越界禁令**：不在 P0 清单内的功能一律不实现；「顺手能加」的 P1/P2 特性只记录到 `docs/task-tiers.md`，待 P0 全绿并确认后再动。

## 相关文档

- [`AGENTS.md`](AGENTS.md) —— 项目总规范：模块边界、全局开发规则、分支策略与协作约束
- [`docs/architecture.md`](docs/architecture.md) —— 总体架构说明
- [`docs/interface-contract.md`](docs/interface-contract.md) —— 模块间接口契约（跨模块通信的唯一约定）
- [`docs/task-tiers.md`](docs/task-tiers.md) —— 任务分级清单（P0/P1/P2）
