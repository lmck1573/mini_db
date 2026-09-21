# storage_engine（存储引擎）开发规范

## 模块职责

在页式存储之上实现"表堆"：管理记录与页的映射、行序列化/反序列化、页内槽位分配、表的扩展与删除标记。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `table_name` / `values` | 来自 executor |
| 输出 | `Iterator[Row]` / `int` | 扫描输出 / row_id 或影响行数 |

## 接口定义

```python
class StorageEngine:
    def create_table(self, table_name: str, columns: list[ColumnDef]) -> int: ...  # 返回 root_page_id
    def open_table(self, table_name: str, columns: list[ColumnDef], root_page_id: int) -> None: ...  # 重启恢复
    def insert_row(self, table_name: str, values: list[Value]) -> int: ...
    def scan_table(self, table_name: str) -> Iterator[Row]: ...     # 跳过删除行
    def delete_rows(self, table_name: str, predicate) -> int: ...
    def get_table_schema(self, table_name: str) -> list[ColumnDef]: ...
    def has_table(self, table_name: str) -> bool: ...               # 内存态判断
    def page_exists(self, page_id: int) -> bool: ...                # 磁盘态探测
    def flush(self) -> None: ...
```

> 实现补充（相对接口定义新增）：`open_table`（重启时注册已有表，不重新分配页）、
> `has_table`（内存态）、`page_exists`（磁盘态探测，供 catalog.bootstrap 判断是否
> 首次运行）。`create_table` 返回 `root_page_id`。

## 关键格式（详见 interface-contract.md）

- **行**：`FLAG(1B) | NCOLS(2B) | 逐列 TYPE(1B) LEN(4B) DATA`
- **页**：16B 页头 + 数据区（正向增长）+ 槽目录（反向增长），槽项 `offset(2B) length(2B)`
- 表的数据页以 `next_page_id` 串成链表，首页号记录在目录中
- 删除为**标记删除**（FLAG bit0 = 1），槽位保留

## 依赖

- 依赖：`storage/`（StorageManager）
- 被依赖：`engine/executor`、`engine/catalog`

## 代码规范

- 序列化/反序列化集中在 `row_codec.py` 之类的独立文件中，便于单测
- 插入时当前页空间不足则申请新页并挂到链表尾
- 所有页修改必须通过 `put_page`（保证缓存脏标记）

## 禁止事项

- 禁止跨页存储单条记录（记录不得拆分到两页）
- 禁止物理移动行（删除只用标记）
- 禁止绕过 StorageManager 直连 DiskManager

## 完成标准

- [x] 行序列化/反序列化往返无损（含 NULL、STRING、FLOAT）
- [x] 单页放满时能自动申请新页
- [x] 标记删除后 scan 不返回该行
- [x] flush 后重启数据仍可扫描
