from __future__ import annotations

import ast
import math
import operator
from typing import Any

_SAFE_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
    "exp": math.exp,
    "pi": math.pi,
    "e": math.e,
}

_SAFE_CONSTS: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
}


class FormulaError(ValueError):
    pass


def _eval_node(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _SAFE_CONSTS:
            return _SAFE_CONSTS[node.id]
        raise FormulaError(f"Unknown variable: {node.id}")
    if isinstance(node, ast.UnaryOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise FormulaError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op(_eval_node(node.operand, env))
    if isinstance(node, ast.BinOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise FormulaError(f"Unsupported binary operator: {type(node.op).__name__}")
        return op(_eval_node(node.left, env), _eval_node(node.right, env))
    if isinstance(node, ast.Call):
        func_name = node.func.id if isinstance(node.func, ast.Name) else None
        if func_name is None or func_name not in _SAFE_FUNCS:
            raise FormulaError(f"Unsupported function: {func_name}")
        args = [_eval_node(a, env) for a in node.args]
        if func_name == "round" and len(args) == 2:
            args[1] = int(args[1])
        return _SAFE_FUNCS[func_name](*args)
    raise FormulaError(f"Unsupported expression: {type(node).__name__}")


def evaluate(expression: str, value: float) -> float:
    expr = expression.strip()
    if not expr:
        return value
    expr = expr.replace("%value%", str(value))
    env = {"value": value, "v": value}
    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval_node(tree.body, env)
        return result
    except FormulaError:
        raise
    except Exception as e:
        raise FormulaError(f"Evaluation error: {e}") from e
