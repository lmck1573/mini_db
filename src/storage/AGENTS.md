# storage（页式存储系统）模块开发规范

## 模块职责

提供以"页"为单位的定长块存储能力：页分配/释放、页读写、缓存管理与替换、磁盘持久化。
对外表现为"一块按 4KB 分页的磁盘"，**不理解表、行、列等数据库语义**。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `page_id: int` / `data: bytes` | 上层请求 |
| 输出 | `bytes`（定长 `PAGE_SIZE`） | 页内容 |

## 子模块与层次

```
cache/  ← 对外统一入口（上层只与 cache 打交道）
  ↓
page/   ← 页分配/回收/空闲页管理
  ↓
disk/   ← 真实文件 IO（data/mini.db）
```

## 接口定义

统一入口（engine 只使用这一层）：

```python
class StorageManager:
    def get_page(self, page_id: int) -> bytes: ...
    def put_page(self, page_id: int, data: bytes) -> None: ...
    def alloc_page(self) -> int: ...
    def free_page(self, page_id: int) -> None: ...
    def flush_all(self) -> None: ...
    def stats(self) -> dict: ...
    def close(self) -> None: ...
```

常量：`PAGE_SIZE = 4096`

## 依赖

- 依赖：无（仅标准库）
- 被依赖：`engine/storage_engine`

## 代码规范

- 所有页 IO 必须是**整页**读写，禁止部分读写的上层可见
- 缓存替换策略通过 `set_policy("LRU"|"FIFO")` 切换，默认 LRU
- 脏页必须标记，`flush_all()` 时统一写回；`close()` 内部自动 flush
- 统计与替换日志输出到 stdout，前缀 `[_CACHE]`，格式见 `docs/interface-contract.md`

## 禁止事项

- 禁止 import `src.compiler` / `src.engine`
- 禁止在存储层解释字节内容（不理解行格式）
- 禁止上层绕过 cache 直接访问 disk

## 完成标准

- [ ] 页分配/释放/读写正确，页编号唯一
- [ ] LRU 与 FIFO 两种策略均可切换并生效
- [ ] 命中率统计与替换日志可输出
- [ ] 程序重启后页内容持久不丢
- [ ] tests/storage 下测试全部通过
