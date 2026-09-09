# disk（磁盘管理器）开发规范

## 模块职责

把单个文件模拟为"磁盘"：按固定页大小做定长块读写，负责文件创建、页追加、落盘。
是存储系统最底层，不做任何缓存与页分配逻辑。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `page_id: int` / `data: bytes` | 页级 IO 请求 |
| 输出 | `bytes`（长度恒为 `PAGE_SIZE`） | 页原始字节 |

## 接口定义

```python
class DiskManager:
    def __init__(self, path: str, page_size: int = 4096): ...
    def read_page(self, page_id: int) -> bytes: ...
    def write_page(self, page_id: int, data: bytes) -> None: ...
    def allocate_page(self) -> int: ...       # 文件尾部追加一页，返回页号
    def page_count(self) -> int: ...
    def close(self) -> None: ...
```

## 实现要点

- 文件不存在时自动创建；已存在时按 `文件大小 / PAGE_SIZE` 推算页数
- `read_page` 越界抛 `DiskError`；读取到的新页返回全 0 填充
- 写页时用 `seek(page_id * PAGE_SIZE)` 定位，写入前校验长度

## 依赖

- 依赖：无
- 被依赖：`storage/page`

## 代码规范

- 所有文件路径通过构造参数传入，禁止硬编码
- 打开的文件句柄在 `close()` 中释放，建议使用上下文管理器

## 禁止事项

- 禁止在 disk 层做缓存或空闲页管理
- 禁止解释页内字节含义

## 完成标准

- [ ] 页读写往返数据一致
- [ ] 重启 DiskManager 后已写页内容仍在（持久化）
- [ ] 越界页号抛出明确异常
