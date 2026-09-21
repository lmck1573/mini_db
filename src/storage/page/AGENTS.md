# page（页管理器）开发规范

## 模块职责

管理页的分配与回收，向上层提供"页"这一抽象，屏蔽文件细节；维护空闲页链表保证页编号唯一。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `page_id` / `data: bytes` | 来自 cache 层 |
| 输出 | `bytes` / `int`（新页号） | 页内容 / 新分配的页号 |

## 接口定义

```python
class PageManager:
    def alloc_page(self) -> int: ...                      # 复用空闲页，否则新建
    def free_page(self, page_id: int) -> None: ...        # 归还页，加入空闲链表
    def read_page(self, page_id: int) -> bytes: ...
    def write_page(self, page_id: int, data: bytes) -> None: ...
    def page_count(self) -> int: ...
    def free_list(self) -> list[int]: ...                 # 调试用
```

## 设计要点

- 第 0 页保留为**元数据页**，记录空闲页链表头与页总数
- 空闲页采用"链表式"：每个空闲页头 4 字节存下一个空闲页号，`-1` 表示链尾
- 页大小固定 4096 B，写入前必须补齐/校验长度，长度不符抛出 `PageError`

元数据页（第 0 页）字节布局：

```
offset 0 : 魔数 b"MDB1"（4 字节，校验文件格式，损坏则抛 PageError）
offset 4 : free_head（4 字节小端，空闲链表头页号，-1 表示空）
offset 8 : page_count（4 字节小端，逻辑页总数，含第 0 页）
其余字节保留为 0
```

## 依赖

- 依赖：`storage/disk`
- 被依赖：`storage/cache`

## 代码规范

- 页号从 0 开始，单调递增，不得复用已分配页号
- 所有页数据长度必须等于 `PAGE_SIZE`

## 禁止事项

- 禁止缓存页内容（缓存是 cache/ 的职责）
- 禁止对外暴露文件句柄

## 完成标准

- [ ] 分配/释放后空闲链表状态正确
- [ ] 页内容读写往返一致
- [ ] 释放后再次分配能复用页号
