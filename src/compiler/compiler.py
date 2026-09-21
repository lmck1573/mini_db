"""SQL 编译器门面：串起 词法 -> 语法 -> 语义 -> 计划 四个阶段。

本模块是 compiler 对外的统一出口，engine 通过它拿到执行计划。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .errors import CompileError
from .lexer import tokenize
from .lexer.token import Token, TokenType
from .parser.ast_nodes import Statement
from .parser.parser import Parser
from .planner.planner import plan
from .planner.plan_nodes import PlanNode
from .semantic.analyzer import analyze
from .semantic.catalog import Catalog


@dataclass
class StatementResult:
    """单条语句的编译结果。"""

    index: int
    sql: str  # 由 Token 词素重建的语句文本（用于展示）
    tokens: List[Token]
    ast: Optional[Statement]
    semantic_ok: bool = False
    message: str = ""
    plan: Optional[PlanNode] = None
    error: Optional[CompileError] = None


class SQLCompiler:
    """SQL 编译器：输入 SQL 文本，输出每条语句的 Token 流 / AST / 语义结论 / 执行计划。"""

    def __init__(self, catalog: Optional[Catalog] = None):
        self.catalog = catalog if catalog is not None else Catalog()

    def compile(self, sql_text: str) -> List[StatementResult]:
        """编译全部语句；遇到第一条错误即抛出 CompileError。"""
        results, error = self._compile(sql_text, safe=False)
        if error is not None:
            raise error
        return results

    def compile_safe(self, sql_text: str) -> Tuple[List[StatementResult], Optional[CompileError]]:
        """编译全部语句；出错时返回已成功部分与错误对象，不抛出。"""
        return self._compile(sql_text, safe=True)

    def _compile(self, sql_text: str, safe: bool) -> Tuple[List[StatementResult],
                                                           Optional[CompileError]]:
        try:
            tokens = tokenize(sql_text)          # 词法错误直接向上抛（还没有语句粒度）
            parser = Parser(tokens)
            statements = parser.parse()          # 语法错误直接向上抛
        except CompileError as err:
            if safe:
                return [], err
            raise

        results: List[StatementResult] = []
        for i, stmt in enumerate(statements):
            start, end = parser.token_ranges[i]
            stmt_tokens = tokens[start:end]
            result = StatementResult(
                index=i + 1,
                sql=_rebuild_sql(stmt_tokens),
                tokens=stmt_tokens,
                ast=stmt,
            )
            results.append(result)
            try:
                result.message = analyze([stmt], self.catalog)[0]
                result.semantic_ok = True
                result.plan = plan(stmt, self.catalog)
            except CompileError as err:
                result.error = err
                result.message = str(err)
                if safe:
                    return results, err
                raise
        return results, None


def _rebuild_sql(tokens: List[Token]) -> str:
    """由 Token 词素重建语句文本（仅用于展示，不做精确还原）。"""
    parts: List[str] = []
    for i, token in enumerate(tokens):
        if token.type is TokenType.EOF:
            continue
        lexeme = token.lexeme
        if i > 0 and lexeme not in (",", ")", ";") and tokens[i - 1].lexeme != "(":
            parts.append(" ")
        parts.append(lexeme)
    return "".join(parts)
