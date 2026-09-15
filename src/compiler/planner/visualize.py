"""执行计划可视化：把逻辑计划树渲染为自包含的 HTML（内嵌 SVG 树图）。

用法：
    from src.compiler.planner.visualize import plan_to_html, write_plan_html
    html = plan_to_html(plan, title="SELECT 计划")
    write_plan_html(plan, "plan.html")

也可以直接用计划的便捷方法：`plan.to_html()`。

渲染特点：
- 自底向上的整齐树布局（叶子按中序排开，父节点居中于子节点之上）；
- 按算子类型着色，不同算子一眼可辨；
- 每个节点带 title 提示，悬停可看该节点的 JSON 结构；
- 输出为单个 HTML 文件，无外部依赖，可直接用浏览器打开或贴进报告。
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any, List, Tuple

from .plan_nodes import PlanNode

# 画布参数
_MARGIN_X = 40
_MARGIN_TOP = 42
_STEP_Y = 104
_NODE_HEIGHT = 62
_MIN_NODE_WIDTH = 132
_GAP_X = 26

# 算子配色（浅色主题）：(边框, 底色, 标题色)
_PALETTE = {
    "ddl": ("#7c6cf0", "#eeeaff", "#4a3fbf"),       # CreateTable / Insert / Delete
    "scan": ("#2f80ed", "#e6f0ff", "#1a5fb4"),      # SeqScan
    "filter": ("#f2994a", "#fff2e3", "#b8600f"),    # Filter
    "project": ("#27ae60", "#e6f7ee", "#1b7a44"),   # Project
    "join": ("#eb5757", "#ffe9e9", "#b32b2b"),      # Join
    "aggregate": ("#00a3a3", "#e0f7f7", "#017070"),  # Aggregate
    "sort": ("#6b7a90", "#eef2f7", "#44546a"),      # Sort
    "other": ("#9aa4b2", "#f1f3f6", "#5b6472"),
}

_OP_KIND = {
    "CreateTable": "ddl",
    "Insert": "ddl",
    "Delete": "ddl",
    "SeqScan": "scan",
    "Filter": "filter",
    "Project": "project",
    "Join": "join",
    "Aggregate": "aggregate",
    "Sort": "sort",
}


@dataclass
class _Node:
    """布局用的中间节点。"""

    title: str
    details: List[str]
    kind: str
    payload: dict
    children: List["_Node"]
    depth: int = 0
    order: float = 0.0
    width: float = 0.0
    x: float = 0.0
    y: float = 0.0


def _text_width(text: str) -> float:
    """粗略估算文本像素宽度（中文按全角计）。"""
    total = 0.0
    for ch in text:
        total += 14.0 if ord(ch) > 0x2000 else 7.6
    return total


def _node_info(plan: PlanNode) -> Tuple[str, List[str]]:
    """返回（标题，细节行）。"""
    op = plan.op
    if op == "CreateTable":
        cols = ", ".join(c["name"] + " " + c["type"] for c in plan.columns)
        return "CreateTable", [f"table = {plan.table_name}", cols]
    if op == "Insert":
        return "Insert", [f"table = {plan.table_name}",
                          f"columns = {', '.join(plan.columns)}",
                          f"rows = {len(plan.rows)}"]
    if op == "Delete":
        pred = "ALL" if plan.predicate is None else str(plan.predicate)
        return "Delete", [f"table = {plan.table_name}", f"where {pred}"]
    if op == "SeqScan":
        details = [f"table = {plan.table_name}"]
        if plan.source:
            details.append(f"as {plan.source}")
        return "SeqScan", details
    if op == "Filter":
        return "Filter", [f"predicate = {plan.predicate}"]
    if op == "Project":
        if plan.exprs is not None:
            pairs = []
            for expr, label in zip(plan.exprs, plan.labels or []):
                text = str(expr)
                pairs.append(label if text == label else f"{text} AS {label}")
            details = [", ".join(pairs)]
        else:
            details = ["*" if plan.columns == "*" else ", ".join(plan.columns)]
        return "Project", details
    if op == "Join":
        details = []
        if plan.on is not None:
            details.append(f"on {plan.on}")
        details.append("nested-loop")
        return f"Join {plan.join_type}", details
    if op == "Aggregate":
        group = ", ".join(plan.group_labels) if plan.group_labels else "(全局)"
        aggs = ", ".join(str(a) for a in plan.aggregates)
        return "Aggregate", [f"group by: {group}", f"aggs: {aggs}"]
    if op == "Sort":
        return "Sort", [", ".join(str(k) for k in plan.keys)]
    return op, []


def _children(plan: PlanNode) -> List[PlanNode]:
    if hasattr(plan, "child"):
        return [plan.child]
    if plan.op == "Join":
        return [plan.left, plan.right]
    return []


def _build(plan: PlanNode) -> _Node:
    title, details = _node_info(plan)
    node = _Node(
        title=title,
        details=details,
        kind=_OP_KIND.get(plan.op, "other"),
        payload=plan.to_dict(),
        children=[_build(c) for c in _children(plan)],
    )
    widest = max([_text_width(title)] +
                 [_text_width(d) for d in details] + [0])
    node.width = max(_MIN_NODE_WIDTH, widest + 28)
    return node


def _layout(node: _Node, depth: int, counter: List[float]) -> None:
    """自底向上布局：叶子按中序排开，父节点居中于最左/最右子节点之间。"""
    node.depth = depth
    if not node.children:
        node.order = counter[0]
        counter[0] += 1.0
        return
    for child in node.children:
        _layout(child, depth + 1, counter)
    node.order = (node.children[0].order + node.children[-1].order) / 2.0


def _assign_xy(node: _Node, step_x: float) -> None:
    node.x = _MARGIN_X + node.order * step_x + node.width / 2.0
    node.y = _MARGIN_TOP + node.depth * _STEP_Y
    for child in node.children:
        _assign_xy(child, step_x)


def _max_depth(node: _Node) -> int:
    if not node.children:
        return node.depth
    return max(_max_depth(c) for c in node.children)


def _nodes(node: _Node):
    yield node
    for child in node.children:
        yield from _nodes(child)


def plan_to_svg(plan: PlanNode) -> str:
    """把计划渲染为一段内嵌 SVG（不包含 <svg> 之外的包装）。"""
    root = _build(plan)
    counter = [0.0]
    _layout(root, 0, counter)

    widest = max(n.width for n in _nodes(root))
    step_x = widest + _GAP_X
    # 根节点居中：给整体加一点左侧留白
    _assign_xy(root, step_x)

    max_x = max(n.x + n.width / 2 for n in _nodes(root)) + _MARGIN_X
    max_y = _MARGIN_TOP + (_max_depth(root) + 1) * _STEP_Y
    edges: List[str] = []
    boxes: List[str] = []

    for node in _nodes(root):
        if node.children:
            parent_bottom = node.y + _NODE_HEIGHT / 2
            for child in node.children:
                child_top = child.y - _NODE_HEIGHT / 2
                mid_y = (parent_bottom + child_top) / 2
                path = (f"M {node.x:.1f} {parent_bottom:.1f} "
                        f"V {mid_y:.1f} H {child.x:.1f} V {child_top:.1f}")
                edges.append(f'<path d="{path}" fill="none" stroke="#c2c9d6" '
                             f'stroke-width="1.6"/>')
        boxes.append(_render_box(node))

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {max_x:.0f} {max_y:.0f}" '
        f'width="{max_x:.0f}" height="{max_y:.0f}" role="img" aria-label="执行计划树">\n'
        + "\n".join(edges) + "\n" + "\n".join(boxes) + "\n</svg>"
    )


def _render_box(node: _Node) -> str:
    border, fill, title_color = _PALETTE.get(node.kind, _PALETTE["other"])
    left = node.x - node.width / 2
    top = node.y - _NODE_HEIGHT / 2
    tooltip = html.escape(json.dumps(node.payload, ensure_ascii=False))
    lines = [f'<g class="node">',
             f'<title>{tooltip}</title>',
             f'<rect x="{left:.1f}" y="{top:.1f}" width="{node.width:.1f}" '
             f'height="{_NODE_HEIGHT}" rx="9" fill="{fill}" stroke="{border}" '
             f'stroke-width="1.8"/>']
    # 标题
    lines.append(f'<text x="{node.x:.1f}" y="{top + 22:.1f}" text-anchor="middle" '
                 f'font-size="14" font-weight="700" fill="{title_color}">'
                 f'{html.escape(node.title)}</text>')
    # 细节：最多两行，超出省略
    y = top + 40
    for detail in node.details[:2]:
        text = detail if len(detail) <= 30 else detail[:29] + "…"
        lines.append(f'<text x="{node.x:.1f}" y="{y:.1f}" text-anchor="middle" '
                     f'font-size="11.5" fill="#4a5568">{html.escape(text)}</text>')
        y += 15
    lines.append("</g>")
    return "\n".join(lines)


def _legend() -> str:
    names = {
        "ddl": "DDL/DML", "scan": "顺序扫描", "filter": "过滤",
        "project": "投影", "join": "连接", "aggregate": "聚合", "sort": "排序",
    }
    chips = []
    for kind, label in names.items():
        border, fill, title_color = _PALETTE[kind]
        chips.append(
            f'<span class="chip" style="background:{fill};border-color:{border};'
            f'color:{title_color}">{label}</span>')
    return '<div class="legend">' + "".join(chips) + "</div>"


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #f6f7fb;
    --panel: #ffffff;
    --text: #1f2733;
    --muted: #6b7280;
    --line: #e3e7ef;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14171c;
      --panel: #1c2027;
      --text: #eef1f6;
      --muted: #9aa4b2;
      --line: #2b313a;
    }}
    .canvas {{ background: #22262e; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 28px 24px 40px;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
                 "PingFang SC", sans-serif;
  }}
  h1 {{ font-size: 19px; margin: 0 0 6px; }}
  .sub {{ margin: 0 0 18px; color: var(--muted); font-size: 13px; }}
  .sub pre {{
    margin: 8px 0 0; padding: 10px 12px; background: var(--panel);
    border: 1px solid var(--line); border-radius: 8px; font-size: 12.5px;
    white-space: pre-wrap; word-break: break-all;
  }}
  .canvas {{
    background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
    padding: 18px; overflow-x: auto;
  }}
  .canvas svg {{ display: block; margin: 0 auto; }}
  .legend {{ margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px; }}
  .chip {{
    font-size: 12px; padding: 3px 10px; border-radius: 999px;
    border: 1px solid; font-weight: 600;
  }}
  .node {{ cursor: default; }}
  .node rect {{ transition: filter .15s ease; }}
  .node:hover rect {{ filter: drop-shadow(0 3px 8px rgba(0,0,0,.18)); }}
  footer {{ margin-top: 20px; color: var(--muted); font-size: 12px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="sub">
  生成的计划树（自顶向下阅读，根节点在最上方）。悬停任意节点可查看其 JSON 结构。
  <pre>{sql}</pre>
</div>
<div class="canvas">
{svg}
</div>
{legend}
<footer>Mini-DB · 模块一 SQL 编译器 · 计划可视化</footer>
</body>
</html>
"""


def plan_to_html(plan: PlanNode, title: str = "执行计划", sql: str = "") -> str:
    """把计划渲染为完整的自包含 HTML 文本。"""
    return _HTML_TEMPLATE.format(
        title=html.escape(title),
        sql=html.escape(sql) if sql else "（未提供 SQL 文本）",
        svg=plan_to_svg(plan),
        legend=_legend(),
    )


def write_plan_html(plan: PlanNode, path: str, title: str = "执行计划",
                    sql: str = "") -> str:
    """把计划写入 HTML 文件，返回文件路径。"""
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(plan_to_html(plan, title=title, sql=sql))
    return path
