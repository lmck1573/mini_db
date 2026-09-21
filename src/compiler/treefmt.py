"""通用树形渲染工具：AST 与执行计划共用。"""

from __future__ import annotations

from typing import List, Optional


class TreeNode:
    """轻量树节点，仅用于文本渲染。"""

    def __init__(self, label: str, children: Optional[List["TreeNode"]] = None):
        self.label = label
        self.children = children or []

    def add(self, child: "TreeNode") -> "TreeNode":
        self.children.append(child)
        return self


def render_tree(root: TreeNode) -> str:
    """把树渲染为带 ├─ └─ 连线的多行文本。"""
    lines: List[str] = []

    def walk(node: TreeNode, prefix: str, is_last: bool, is_root: bool) -> None:
        connector = "" if is_root else ("└─ " if is_last else "├─ ")
        lines.append(prefix + connector + node.label)
        child_prefix = prefix + ("" if is_root else ("   " if is_last else "│  "))
        for i, child in enumerate(node.children):
            walk(child, child_prefix, i == len(node.children) - 1, False)

    walk(root, "", True, True)
    return "\n".join(lines)
