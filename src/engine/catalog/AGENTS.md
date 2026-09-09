# catalog（系统目录）开发规范

## 模块职责

维护数据库元数据（表名、列名、列类型、列长度、列序、首页 ID），并将目录本身作为一张特殊表通过存储引擎持久化。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | 表名 / 列定义 | 来自 executor 与恢复流程 |
| 输出 | `TableSchema` / `list[str]` | 供编译器语义分析与算子执行使用 |

## 接口定义

```python
CATALOG_TABLE = "__catalog__"

class SystemCatalog:
    def bootstrap(self) -> None: ...                                   # 首次运行时初始化
    def create_table(self, name: str, columns: list[ColumnDef]) -> int: ...
    def get_table(self, name: str) -> TableSchema | None: ...
    def list_tables(self) -> list[str]: ...
    def drop_table(self, name: str) -> None: ...
    def to_compiler_catalog(self) -> Catalog: ...                      # 转为 compiler/semantic 的 Catalog
```

目录表列定义（自身也是行存储）：

| 列 | 类型 |
|----|------|
| table_name | TEXT |
| column_name | TEXT |
| col_type | TEXT |
| col_length | INT |
| ordinal | INT |
| root_page_id | INT |

## 依赖

- 依赖：`engine/storage_engine`（持久化）、`compiler/semantic`（Catalog 数据结构）
- 被依赖：`engine/executor`、`engine`（Database）

## 代码规范

- 表名大小写不敏感，内部统一小写
- 启动顺序：先 bootstrap/加载目录 → 再构造 compiler 的内存 Catalog → 最后接受 SQL
- 目录变更必须即时落盘（或标记脏页，程序退出前 flush）

## 禁止事项

- 禁止把元数据只放内存（必须持久化，课程明确要求）
- 禁止绕过 storage_engine 自己写文件

## 完成标准

- [ ] 新建表后目录可查询到完整列定义
- [ ] 重启程序后目录与表数据均可恢复
- [ ] 重复建表能报出错误
