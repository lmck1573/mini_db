# Mini-DB

课程实训《大型平台软件设计实习》项目：从零分步构建一个简化数据库系统。

## 技术栈

**Python 3.11+**（推荐 3.13），无第三方运行时依赖（测试使用标准库 `unittest`）。

选择 Python 的原因：

- 开发效率高，编译器的 Token/AST/Plan 这类树形结构用 dataclass 表达最简洁；
- 标准库 `collections.OrderedDict` 天然适合实现 LRU 缓存（对应操作系统模块的缓存管理要求）；
- `struct` / `bytes` 便于实现行序列化与页式存储模拟；
- 调试方便，交互式 CLI 便于演示"Token 流 → AST → 语义检查 → 执行计划"的完整链路。

## 目录结构

```
mini-db/
├── AGENTS.md                    # 项目总规范
├── README.md
├── .gitignore
├── docs/
│   ├── architecture.md          # 总体架构说明
│   └── interface-contract.md    # 模块间接口契约
├── src/
│   ├── compiler/                # 模块一：SQL 编译器
│   │   ├── lexer/               #   词法分析
│   │   ├── parser/              #   语法分析
│   │   ├── semantic/            #   语义分析 + Catalog
│   │   └── planner/             #   执行计划生成
│   ├── storage/                 # 模块二：页式存储系统
│   │   ├── page/                #   页管理器
│   │   ├── cache/               #   缓存与替换策略
│   │   └── disk/                #   磁盘文件读写
│   ├── engine/                  # 模块三：数据库系统
│   │   ├── executor/            #   执行算子
│   │   ├── catalog/             #   系统目录（持久化）
│   │   └── storage_engine/      #   存储引擎（行/页映射）
│   └── main.py                  # CLI 入口
├── tests/                       # 测试
└── data/                        # 运行时数据目录（数据库文件）
```

## 快速开始

```bash
# 运行 SQL 编译器（输出 Token 流 / AST / 语义检查 / 执行计划）
python -m src.main --file tests/compiler/sql/valid.sql

# 交互式输入
python -m src.main

# 数据库执行模式（engine：实际执行 SQL 并打印结果集）
python -m src.main --execute
python -m src.main --execute --file demo.sql

# 运行测试（-t . 不可省略）
python -m unittest discover -s tests -t . -p "test_*.py" -v
```

> 以上命令需在项目根目录执行。

### Windows 一键运行（推荐）

Windows 上 `python` 可能是 Microsoft Store 的占位程序（直接运行会报错），且终端默认 GBK 编码会让中文输出乱码。项目已内置启动脚本自动处理这两点（切换到 UTF-8、优先使用 `.venv` 解释器）：

```bat
:: 运行编译器（双击或命令行，参数原样透传给 python -m src.main）
run.bat --file tests\compiler\sql\valid.sql
run.bat                            :: 交互模式

:: 运行全部测试
test.bat
```

命令行（cmd / PowerShell）直接双击或输入脚本名即可；Git Bash 下使用 `./run.sh`、`./test.sh`。

首次使用先创建虚拟环境（一次性）：

```bat
py -m venv .venv
```

（无第三方依赖，无需 `pip install`；`requirements.txt` 仅作占位。）

## 任务分级（当前只做 P0）

完整清单见 [`docs/task-tiers.md`](docs/task-tiers.md)。

| 级别 | 内容 | 状态 |
|------|------|------|
| **P0 必做** | Lexer + Token 位置、四类 SQL、AST、Catalog、语义检查、Logical Plan、错误定位、基础测试 | ✅ 进行中 |
| **P1 进阶** | AND/OR/NOT、复杂表达式、≥2 条优化规则、智能错误诊断、Plan 可视化、隐藏测试通过率 | ⏸ 暂缓 |
| **P2 扩展** | JOIN/GROUP BY、Fuzz Testing、优化框架、代价模型、EXPLAIN、高级错误恢复 | 🚫 不做 |

**越界禁令**：不在 P0 清单内的功能一律不实现；"顺手能加"的 P1/P2 特性只记录到 `docs/task-tiers.md`，待 P0 全绿并确认后再动。

## 分工约定

| 模块 | 内容 | 负责人 |
|------|------|--------|
| 模块一 | SQL 编译器（词法/语法/语义/计划） | 本人 |
| 模块二 | 页式存储系统 | 待定 |
| 模块三 | 数据库执行引擎 | 待定 |

模块间通过 `docs/interface-contract.md` 约定的接口对接，可并行开发：只要契约不变，各模块独立实现即可无缝集成。
