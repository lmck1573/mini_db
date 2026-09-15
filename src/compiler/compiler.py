"""SQL 编译器门面：串起 词法 -> 语法 -> 语义 -> 计划 四个阶段。

本模块是 compiler 对外的统一出口，engine 通过它拿到执行计划。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .errors import CompileError
from .lexer import tokenize
from .lexer.token import Token, TokenType
from .parser.ast_nodes import Explain, Statement
from .parser.parser import Parser
from .planner.planner import plan_with_optimization
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
    plan: Optional[PlanNode] = None          # 优化后的执行计划
    raw_plan: Optional[PlanNode] = None      # 优化前的原始计划（用于对比展示）
    optimizations: List[str] = field(default_factory=list)  # 命中的优化规则
    explain: bool = False                    # 是否为 EXPLAIN（只输出计划，不执行）
    # EXPLAIN 的计划只用于展示，**不放进 plan 字段**，
    # 这样执行引擎看到 plan is None 会自动跳过，不会误执行被解释的语句。
    explain_plan: Optional[PlanNode] = None
    error: Optional[CompileError] = None

    @property
    def display_plan(self) -> Optional[PlanNode]:
        """用于展示的计划（普通语句为优化后计划，EXPLAIN 为被解释语句的计划）。"""
        return self.explain_plan if self.explain else self.plan


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
                explain=isinstance(stmt, Explain),
            )
            results.append(result)
            try:
                result.message = analyze([stmt], self.catalog)[0]
                result.semantic_ok = True
                raw, optimized, applied = plan_with_optimization(stmt, self.catalog)
                result.raw_plan = raw
                result.optimizations = applied
                if result.explain:
                    result.explain_plan = optimized
                else:
                    result.plan = optimized
            except CompileError as err:
                result.error = err
                result.message = str(err)
                if safe:
                    return results, err
                raise
        return results, None


def _rebuild_sql(tokens: List[Token]) -> str:
    """由 Token 词素重建语句文本（仅用于展示，不做精确还原）。"""
    from .lexer.keywords import TYPE_KEYWORDS

    parts: List[str] = []
    previous: Optional[Token] = None
    for token in tokens:
        if token.type is TokenType.EOF:
            continue
        if previous is not None and _needs_space(previous, token, TYPE_KEYWORDS):
            parts.append(" ")
        parts.append(token.lexeme)
        previous = token
    return "".join(parts)


def _needs_space(previous: Token, current: Token, type_keywords) -> bool:
    """决定两个 Token 之间是否需要插入空格，让重建的 SQL 更好读。"""
    if current.lexeme in (",", ")", ";", "."):
        return False
    if previous.lexeme in ("(", "."):
        return False
    if current.lexeme == "(":
        # 类型名或函数名紧跟括号：VARCHAR(20) / COUNT(*)
        if previous.type is TokenType.IDENTIFIER:
            return False
        if previous.type is TokenType.KEYWORD and previous.lexeme.upper() in type_keywords:
            return False
    return True
