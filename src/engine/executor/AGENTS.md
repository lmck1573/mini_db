# executor（执行算子）开发规范

## 模块职责

以火山模型（Volcano / Iterator Model）执行逻辑计划：每个算子实现 `open/next/close`，父算子按需向子算子拉取一行。

## 输入与输出

| 方向 | 类型 | 说明 |
|------|------|------|
| 输入 | `PlanNode` 子树 + `ExecutionContext` | 计划与运行环境 |
| 输出 | `Row | None`（`next()`） | 每次上抛一行，`None` 表示结束 |

## 接口定义

```python
class Operator:
    def open(self) -> None: ...
    def next(self) -> dict | None: ...      # Row 以 dict 表示：{列名: 值}
    def close(self) -> None: ...

def build(plan: PlanNode, ctx: ExecutionContext) -> Operator: ...
```

算子清单：

| 算子 | 行为 |
|------|------|
| `CreateTable` | 通过 catalog 注册表结构，返回影响 0 行 |
| `Insert` | 逐行序列化后交 storage_engine 写入，返回插入行数 |
| `SeqScan` | 遍历表的所有数据页，逐行反序列化并跳过删除标记行 |
| `Filter` | 用 `eval_expr(row, predicate)` 求谓词布尔值过滤 |
| `Project` | 按列投影（`*` 则原样返回） |
| `Delete` | 对命中行置删除标记，返回删除行数 |

## 依赖

- 依赖：`compiler/planner`（PlanNode）、`engine/catalog`、`engine/storage_engine`
- 被依赖：`engine`（Database）

## 代码规范

- 谓词求值统一走 `eval_expr()`，操作数类型按 compiler 的兼容规则提升（INT+FLOAT → FLOAT）
  - `eval_expr` 定义于 `engine/expr.py`，由 Filter 算子与 storage_engine 的 delete_rows 共用
- `InsertOperator` 负责把 `InsertPlan.columns`（可能为子集）按表结构补齐为全列顺序，缺列填 NULL 后写入
- 每个算子不超过 300 行，超过则拆分
- `Row` 在算子间以 `dict[str, Any]` 传递，列顺序由 Project 决定

## 禁止事项

- 禁止在算子中直接调用 storage 的页接口（必须经 storage_engine）
- 禁止一次性把整表读进内存（必须流式 next）

## 完成标准

- [x] 六类算子全部实现
- [x] SELECT + WHERE 结果集正确
- [x] 删除后再查询结果符合预期
- [x] 大量数据（≥1000 行）插入查询无错误
