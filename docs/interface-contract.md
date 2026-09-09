# 模块间接口契约

> 本文件是**唯一**的跨模块通信约定。任何模块间调用必须通过此处定义的接口；若需变更，必须三方确认后同步修改本文件与对应 AGENTS.md。

## 1. Compiler → Engine 接口

### 1.0 调用方式（模块一已实现）

```python
from src.compiler.compiler import SQLCompiler
from src.compiler.semantic import Catalog

compiler = SQLCompiler()                 # 内部自带 Catalog，也可传入复用的 Catalog
results = compiler.compile(sql_text)     # 出错抛 CompileError
for r in results:
    r.sql        # 语句文本
    r.tokens     # Token 列表（含行号列号）
    r.ast        # AST 节点
    r.plan       # 逻辑执行计划（PlanNode）—— engine 消费它

# 出错时不中断、仍需已成功部分的场景（CLI 使用）
results, error = compiler.compile_safe(sql_text)   # error 为 None 表示全部通过

# Catalog 导出/导入，供 engine 持久化
Catalog.from_dict(catalog.to_dict())
```

### 1.1 执行计划节点（Plan Node）

统一使用**树形结构**，每个节点是一个 dataclass，均实现 `to_dict()` / `to_json()`，可无损序列化为 JSON（便于打印、测试与跨模块传递）。

节点类型一览：

| 算子 | 字段 | 含义 |
|------|------|------|
| `CreateTable` | `table_name: str`, `columns: list[ColumnDef]` | 建表，columns 顺序即物理列序 |
| `Insert` | `table_name: str`, `columns: list[str]`, `rows: list[list[Literal]]` | 插入；`columns` 一定是**展开后的完整列名**（省略列名时由 planner 从 Catalog 补全） |
| `SeqScan` | `table_name: str` | 顺序扫描整表 |
| `Filter` | `predicate: Expr`, `child: PlanNode` | 按谓词过滤子节点输出 |
| `Project` | `columns: list[str] \| "*"`, `child: PlanNode` | 投影；`"*"` 表示全列 |
| `Delete` | `table_name: str`, `predicate: Expr \| None` | 删除；`predicate=None` 表示清空表 |

### 1.2 表达式（Expr）结构

谓词与插入值统一用表达式树表示，节点为：

```jsonc
// 字面量
{"type": "Literal", "value": 18, "value_type": "INT"}
{"type": "Literal", "value": "Alice", "value_type": "STRING"}
{"type": "Literal", "value": null, "value_type": "NULL"}
{"type": "Literal", "value": 3.14, "value_type": "FLOAT"}

// 列引用
{"type": "ColumnRef", "name": "age"}

// 二元运算（比较 / 算术 / 逻辑）
{"type": "BinaryOp", "op": ">", "left": {...}, "right": {...}}
{"type": "BinaryOp", "op": "AND", "left": {...}, "right": {...}}

// 一元运算
{"type": "UnaryOp", "op": "NOT", "operand": {...}}
```

约定：
- `op` 取值集合：`= <> != < <= > >= + - * / AND OR`
- `value_type` 取值集合：`INT FLOAT STRING NULL`
- 逻辑运算符统一为 `AND` / `OR`（大写），语义分析阶段已保证比较两侧类型兼容。

### 1.3 计划示例

`SELECT id,name FROM student WHERE age > 18;` 的 JSON 计划：

```json
{
  "op": "Project",
  "columns": ["id", "name"],
  "child": {
    "op": "Filter",
    "predicate": {
      "type": "BinaryOp",
      "op": ">",
      "left":  {"type": "ColumnRef", "name": "age"},
      "right": {"type": "Literal", "value": 18, "value_type": "INT"}
    },
    "child": {"op": "SeqScan", "table_name": "student"}
  }
}
```

S 表达式形式（同一计划）：

```
(Project [id name]
  (Filter (> age 18)
    (SeqScan student)))
```

### 1.4 计划传递方式

- **进程内（默认）**：`SQLCompiler.compile(sql) -> list[StatementPlan]`，engine 直接消费 `PlanNode` 对象树。
- **序列化（可选，便于测试与调试）**：`plan.to_dict()` / `PlanNode.from_dict(d)` / `plan.to_json()`。
- 编译器**只产出计划，不执行**，不持有任何表数据。

### 1.5 列类型（ColumnDef）

```json
{"name": "name", "type": "VARCHAR", "length": 32}
```

`type` 取值：`INT`（4 字节）、`FLOAT`（8 字节）、`VARCHAR`（变长，length 默认 32）、`TEXT`（变长）。

### 1.6 P0 范围边界（模块一当前能力）

支持：`CREATE TABLE` / `INSERT`（含一次多值）/ `SELECT`（`*`、指定列、单条件 WHERE）/ `DELETE`（带或不带 WHERE）。

明确不支持，且遇到即报错而非静默解析：

- `AND / OR / NOT`、算术表达式（`a + 1`）、括号嵌套 —— 报 `SyntaxError`
- `JOIN / GROUP BY / ORDER BY / UPDATE / EXPLAIN / 子查询` —— 报 `SyntaxError`
- 任何查询优化（谓词下推、常量折叠等）—— 不做，计划形状固定为 `Project → Filter → SeqScan`

### 1.7 集成检查清单（engine 侧照此对接）

- [ ] `SQLCompiler().compile(sql)` 能拿到 `results[i].plan`
- [ ] `CreateTablePlan` → 写系统目录（目录本身也通过存储引擎持久化）
- [ ] `InsertPlan.columns` 是展开后的完整列名，直接按此顺序序列化行
- [ ] `SeqScan` 遍历表的所有数据页，跳过删除标记行
- [ ] `Filter` 用 `predicate` 求值；`op` 只有 `= <> != < <= > >=`；`INT` 与 `FLOAT` 比较时提升为 `FLOAT`
- [ ] `Project` 的 `columns == "*"` 时输出全列
- [ ] `DeletePlan.predicate` 为 `null` 表示清空表
- [ ] 启动时用持久化的目录 `Catalog.from_dict(...)` 初始化编译器，保证重启后语义检查仍正确

---

## 2. Engine → Storage 接口

### 2.1 存储引擎接口（engine/storage_engine 对外）

```python
class StorageEngine:
    def create_table(self, table_name: str, columns: list[ColumnDef]) -> None: ...
    def insert_row(self, table_name: str, values: list[Value]) -> int: ...   # 返回 row_id
    def scan_table(self, table_name: str) -> Iterator[Row]: ...              # 跳过已删除行
    def delete_rows(self, table_name: str, predicate) -> int: ...            # 返回删除行数
    def get_table_schema(self, table_name: str) -> list[ColumnDef]: ...
    def flush(self) -> None: ...                                             # 脏页落盘
```

### 2.2 行序列化格式

一条记录（Row）序列化为字节流：

```
FLAG(1B) | NCOLS(2B, 小端) | 逐列: TYPE(1B) | LEN(4B, 小端) | DATA(LEN B)
```

- `FLAG`：bit0 = 删除标记（1 表示已删除），其余位保留
- `TYPE`：`0=NULL, 1=INT, 2=FLOAT, 3=STRING(VARCHAR/TEXT)`
- `INT`：4 字节小端补码；`FLOAT`：8 字节 IEEE754 双精度；`STRING`：UTF-8 字节，`LEN` 为字节数

### 2.3 页格式（Slotted Page）

页大小固定 **4096 B**，布局：

```
┌──────────── 页头 16B ────────────┬──── 数据区（从头增长）───┬── 槽目录（从尾增长）──┐
│ page_id(4) │ free_offset(4) │    │  record bytes ...        │  slot[n]: off(2)+len(2)│
│ slot_count(2) │ next_page_id(4) │                           │                        │
│ reserved(2)                      │                           │                        │
└──────────────────────────────────┴───────────────────────────┴────────────────────────┘
```

- `next_page_id = -1` 表示链表尾；表堆用首页 + next 指针串成页链表
- 删除采用**标记删除**（置 FLAG bit0），槽位保留，避免行移动

---

## 3. Storage 内部接口

### 3.1 磁盘管理器（disk/）

```python
class DiskManager:
    def __init__(self, path: str, page_size: int = 4096): ...
    def read_page(self, page_id: int) -> bytes: ...      # 返回定长 page_size 字节
    def write_page(self, page_id: int, data: bytes) -> None: ...
    def allocate_page(self) -> int: ...                  # 文件末尾追加新页，返回新 page_id
    def page_count(self) -> int: ...
    def close(self) -> None: ...
```

### 3.2 页管理器（page/）

```python
class PageManager:
    def alloc_page(self) -> int: ...        # 优先复用空闲页链表，否则向 disk 申请
    def free_page(self, page_id: int) -> None: ...
    def read_page(self, page_id: int) -> bytes: ...
    def write_page(self, page_id: int, data: bytes) -> None: ...
```

### 3.3 缓存管理器（cache/）

```python
class CacheManager:
    def get_page(self, page_id: int) -> bytes: ...        # 命中则 hits+1，否则 misses+1 并按需替换
    def put_page(self, page_id: int, data: bytes) -> None: ...
    def flush_page(self, page_id: int) -> None: ...       # 脏页写回
    def flush_all(self) -> None: ...
    def stats(self) -> dict: ...                          # {"hits":..,"misses":..,"hit_rate":..,"evictions":..}
    def set_policy(self, policy: str) -> None: ...        # "LRU" | "FIFO"
```

替换日志格式（便于实验报告展示）：

```
[_CACHE] MISS page=3 policy=LRU action=load
[_CACHE] EVICT page=1 (dirty, flushed) policy=LRU
[_CACHE] HIT  page=1
```

### 3.4 统计信息约定

`stats()` 返回：`hits`、`misses`、`hit_rate`（保留 4 位小数）、`evictions`、`flushes`、`capacity`、`size`。

---

## 4. 系统目录（catalog）

- 目录表名：`__catalog__`
- 目录同样以**行**的形式存放在存储引擎管理的表中，靠存储引擎持久化
- 目录列：`(table_name STRING, column_name STRING, col_type STRING, col_length INT, ordinal INT, root_page_id INT)`
- engine/catalog 提供：`create_table_meta() / get_table_meta(table_name) / drop_table_meta() / list_tables()`

> compiler/semantic 内部也维护一份**内存 Catalog**，仅用于编译期语义校验；它与 engine 的持久目录内容一致，但**不直接读写磁盘**。engine 在启动时用持久目录初始化编译器所需的 Catalog。

---

## 5. 运行与验证（模块一当前状态）

```bash
cd mini-db
python -m src.main --file tests/compiler/sql/valid.sql                # 四阶段全量输出
python -m src.main --file tests/compiler/sql/valid.sql --plan-format json
python -m src.main                                                    # 交互模式
python -m unittest discover -s tests -t . -p "test_*.py"              # 75 例测试
```

注意事项：

1. 必须在 **mini-db 目录**下执行（模块以 `src.` 开头）
2. 测试命令的 `-t .` 不能省略，否则 `import src` 失败
3. 错误输出统一为 `[错误类型，位置，原因说明]`（见根 AGENTS.md 错误处理规范）
