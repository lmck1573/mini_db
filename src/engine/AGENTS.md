# engine（数据库系统）模块开发规范

## 模块职责

把编译器产出的逻辑执行计划落到页式存储上执行：管理系统目录、完成行与页的映射、实现执行算子，并通过 CLI 对外提供交互。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `PlanNode`（来自 compiler） | 逻辑执行计划 |
| 输出 | 结果集 `list[Row]` / 影响行数 / 错误 | 供 CLI 打印 |

## 子模块

```
executor/        执行算子（火山模型 open/next/close）
catalog/         系统目录（__catalog__ 表，持久化元数据）
storage_engine/  表堆、行序列化、页链表管理
```

## 接口定义

```python
class Database:
    def __init__(self, data_dir: str = "data"): ...
    def execute(self, sql: str) -> QueryResult: ...   # 串联 compiler 与本模块
    def flush(self) -> None: ...
    def close(self) -> None: ...

@dataclass
class QueryResult:
    success: bool
    rows: list[dict] | None
    message: str
```

## 依赖

- 依赖：`compiler/`（计划）、`storage/`（页 IO）
- 被依赖：`src/main.py`（CLI）

## 代码规范

- 算子统一实现 `open() / next() / close()`，`next()` 返回 `Row | None`
- 执行期错误抛 `ExecutionError`，由 CLI 统一捕获打印
- 启动时从 `catalog` 恢复元数据，并据此构造 compiler 需要的内存 Catalog

## 禁止事项

- 禁止绕过 storage 接口直接读写文件
- 禁止在 engine 中重新实现词法/语法分析（必须复用 compiler）

## 完成标准

- [ ] CREATE TABLE / INSERT / SELECT(WHERE) / DELETE 四条语句执行正确
- [ ] 系统目录作为特殊表持久化
- [ ] 重启程序后数据与元数据不丢失
- [ ] tests/engine 集成测试通过
