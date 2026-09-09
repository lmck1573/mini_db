"""编译器统一异常定义。

所有词法/语法/语义/计划阶段的错误都继承 CompileError，
并统一格式化为课程要求的三元组形式：[错误类型，位置，原因说明]。
"""

from __future__ import annotations


class CompileError(Exception):
    """编译器错误基类。"""

    error_type = "CompileError"

    def __init__(self, message: str, line: int | None = None, col: int | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def position(self) -> str:
        """返回可读的位置描述，无位置信息时为 '<unknown>'."""
        if self.line is None or self.col is None:
            return "<unknown>"
        return f"Line {self.line} Col {self.col}"

    def format(self) -> str:
        """格式化为 [错误类型，位置，原因说明]。"""
        return f"[{self.error_type}, {self.position()}, {self.message}]"

    def __str__(self) -> str:  # 直接 print 异常时也是三元组格式
        return self.format()


class LexicalError(CompileError):
    """词法错误：非法字符、未闭合字符串等。"""

    error_type = "LexicalError"


class SyntaxErr(CompileError):
    """语法错误：Token 序列不符合文法（命名加 Err 以避开内置 SyntaxError）。"""

    error_type = "SyntaxError"


class SemanticError(CompileError):
    """语义错误：表/列不存在、类型不匹配、列数不一致等。"""

    error_type = "SemanticError"


class PlannerError(CompileError):
    """计划生成错误：不支持的语法或缺失的语义信息。"""

    error_type = "PlannerError"
