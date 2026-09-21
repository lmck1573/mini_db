# Mini-DB 总体架构说明

## 一、分层架构

```
┌─────────────────────────────────────────────────────────┐
│  CLI / 交互层 (src/main.py)                              │
│  接收 SQL 文本，格式化输出各阶段结果或错误                │
└─────────────────────────────────────────────────────────┘
                    │                      ▲
                    ▼                      │ 结果集 / 错误信息
┌─────────────────────────────────────────────────────────┐
│  模块三：engine/ 数据库系统                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │ executor │  │ catalog  │  │ storage_engine        │  │
│  │ 执行算子 │  │ 系统目录 │  │ 行↔页序列化/表堆管理  │  │
│  └──────────┘  └──────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────┘
        ▲ 逻辑执行计划(Plan)          │ 页级读写
        │                              ▼
┌───────────────┐      ┌──────────────────────────────────┐
│ 模块一：      │      │ 模块二：storage/ 页式存储系统     │
│ compiler/     │      │ ┌────────┐ ┌────────┐ ┌────────┐ │
│ lexer→parser  │      │ │ cache  │→│ page   │→│ disk   │ │
│ →semantic     │      │ │LRU/FIFO│ │分配/回收│ │文件IO  │ │
│ →planner      │      │ └────────┘ └────────┘ └────────┘ │
└───────────────┘      └──────────────────────────────────┘
                                                    │
                                                    ▼
                                            data/mini.db (4KB 页)
```

compiler 与 storage 互不依赖（两条独立竖线），engine 是唯一的汇合点。这样设计的好处：三个模块可以由不同人并行开发，只要遵守 `interface-contract.md` 即可集成。

## 二、模块一：SQL 编译器（compiler/）

四阶段流水线，每阶段输入上一阶段输出：

| 阶段 | 输入 | 输出 | 关键产物 |
|------|------|------|---------|
| 词法分析 | SQL 字符流 | Token 流 | `[种别码, 词素值, 行号, 列号]` |
| 语法分析 | Token 流 | AST | 递归下降，语句级节点 |
| 语义分析 | AST | 带注解的 AST | Catalog（表/列/类型元数据） |
| 计划生成 | AST | 逻辑执行计划 | Plan 树（JSON / S 表达式 / 树形） |

**支持的语句**：`CREATE TABLE`、`INSERT`、`SELECT`（含单条件 WHERE）、`DELETE`（含单条件 WHERE）。

**范围约束**：依据 `docs/task-tiers.md`，当前只实现 P0 必做项。WHERE 仅支持单条件 `列 比较符 值`，不支持 AND/OR/NOT 与算术表达式，不做任何查询优化；`UPDATE`、`ORDER BY`、`GROUP BY`、`JOIN`、`EXPLAIN` 等均不在本阶段范围内。

## 三、模块二：页式存储系统（storage/）

- **disk/**：把单个文件 `data/mini.db` 看成"磁盘"，文件按 4KB 切分为页，提供 `read_page/write_page/allocate_page`。
- **page/**：页管理器，维护空闲页列表（可用位图或文件头页记录），负责页的分配与释放。
- **cache/**：页缓存，固定容量（如 64 页），支持 LRU / FIFO 替换，维护脏页标记、命中率统计与替换日志。对外提供 `get_page/flush_page/flush_all`。

层次关系：`cache → page → disk`，上层只与 cache 打交道。

## 四、模块三：数据库系统（engine/）

- **catalog/**：系统目录（`__catalog__`），本身作为一张特殊表通过存储引擎持久化，记录表名、列名、列类型、首页 ID。
- **storage_engine/**：表堆（TableHeap），管理"记录 → 页"的映射，负责行序列化/反序列化、页内槽位管理、表扩展（申请新页）、删除标记。
- **executor/**：火山模型（Volcano / Iterator Model）执行算子，每个算子实现 `open() → next() → close()`：
  - `CreateTable`：写系统目录
  - `Insert`：序列化行并写入表堆
  - `SeqScan`：顺序扫描表的所有数据页，逐行吐出
  - `Filter`：按谓词过滤行
  - `Project`：按目标列投影
  - `Delete`：定位命中行并打删除标记

## 五、端到端执行流程（以 SELECT 为例）

```
SELECT id,name FROM student WHERE age > 18;

compiler:  Token流 → AST(Select) → 语义检查(表/列/类型) → Plan
           Project([id,name])
             └─ Filter(age > 18)
                  └─ SeqScan(student)

engine:    Project.next()
             → Filter.next() 循环取 SeqScan.next()
             → SeqScan 通过 storage_engine 遍历 student 的数据页
             → storage: get_page(page_id)（命中缓存则不落盘 IO）
             → 反序列化为 Row，逐层上抛

CLI:       打印结果集表格
```

## 六、错误传播路径

| 来源 | 形式 | 处理 |
|------|------|------|
| lexer / parser / semantic | `[错误类型，位置，原因说明]` | 编译器阶段直接终止该语句，CLI 打印 |
| storage | 异常（`StorageError` 体系） | engine 捕获，转为执行错误 |
| engine | 异常（`ExecutionError` 体系） | CLI 统一捕获并打印 |

## 七、持久化

- 所有表数据与系统目录均通过 storage 落盘到 `data/mini.db`；
- 缓存脏页在 `flush_all()` 或程序正常退出时写回；
- 程序重启后，engine 先从系统目录页恢复元数据，再对外提供服务。
