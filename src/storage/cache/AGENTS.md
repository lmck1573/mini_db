# cache（缓存管理器）开发规范

## 模块职责

在内存中缓存磁盘页，按 LRU / FIFO 策略替换，统计命中率并输出替换日志，向上层提供 `get_page/flush_page`。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `page_id: int`，可选 `data: bytes` | 页请求 / 写回请求 |
| 输出 | `bytes` / `dict`（统计） | 页内容 / 命中率等统计 |

## 接口定义

```python
class CacheManager:
    def __init__(self, page_mgr: PageManager, capacity: int = 64, policy: str = "LRU"): ...
    def get_page(self, page_id: int) -> bytes: ...
    def put_page(self, page_id: int, data: bytes) -> None: ...   # 置脏
    def flush_page(self, page_id: int) -> None: ...
    def flush_all(self) -> None: ...
    def evict(self) -> int: ...                                   # 返回被淘汰页号
    def set_policy(self, policy: str) -> None: ...
    def stats(self) -> dict: ...
```

`stats()` 返回：`hits / misses / hit_rate / evictions / flushes / capacity / size`

## 实现要点

- LRU：使用 `collections.OrderedDict`，`get_page` 命中时 `move_to_end`
- FIFO：同样用 `OrderedDict` 但命中时**不**移动顺序
- 淘汰脏页必须先写回；日志格式：
  ```
  [_CACHE] HIT  page=1
  [_CACHE] MISS page=3 policy=LRU action=load
  [_CACHE] EVICT page=1 (dirty, flushed) policy=LRU
  ```

## 依赖

- 依赖：`storage/page`
- 被依赖：`engine/storage_engine`（经 `StorageManager` 封装）

## 代码规范

- 替换策略用枚举/字符串常量，禁止 if-else 散落
- 每次替换必须写日志（便于实验报告截图）

## 禁止事项

- 禁止直接操作文件（必须经 page/disk）
- 禁止无限增长（capacity 必须生效）

## 完成标准

- [ ] LRU / FIFO 两种策略行为正确且有测试对比
- [ ] 命中率统计准确
- [ ] 脏页能被正确写回，flush_all 后磁盘内容一致
