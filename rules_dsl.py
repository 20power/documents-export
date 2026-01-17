# -*- coding: utf-8 -*-
"""
A tiny expression evaluator for rule DSL using Python AST with strict allowlist.
Supported:
- literals: str, int, float, bool, None
- names: variables from context (row)
- ops: + - * / // % ** (for numeric), comparisons (== != < <= > >=), 'in'/'not in'
- bool ops: and/or/not
- parentheses, lists/tuples/dicts
"""
import ast
from typing import Any, Dict

ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.IfExp,
    ast.Compare, ast.Call, ast.Num, ast.Str, ast.Bytes, ast.List, ast.Tuple,
    ast.Dict, ast.Set, ast.NameConstant, ast.Name, ast.Load, ast.Subscript,
    ast.Index, ast.Slice, ast.ExtSlice, ast.Constant, ast.Attribute,
)
ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
ALLOWED_BOOL = (ast.And, ast.Or)
ALLOWED_UNARY = (ast.Not, ast.USub, ast.UAdd)
ALLOWED_CMPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot)

SAFE_BUILTINS = {
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
}

class UnsafeExpression(Exception):
    pass

def _eval(node: ast.AST, ctx: Dict[str, Any]):
    if isinstance(node, ast.Expression):
        return _eval(node.body, ctx)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return ctx.get(node.id, None)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ALLOWED_BINOPS):
        left = _eval(node.left, ctx)
        right = _eval(node.right, ctx)
        return eval(compile(ast.Expression(node), "<binop>", "eval"), {"__builtins__": {}}, ctx)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ALLOWED_UNARY):
        operand = _eval(node.operand, ctx)
        return eval(compile(ast.Expression(node), "<unary>", "eval"), {"__builtins__": {}}, ctx)

    if isinstance(node, ast.BoolOp) and isinstance(node.op, ALLOWED_BOOL):
        values = [_eval(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        else:
            return any(values)

    if isinstance(node, ast.Compare) and all(isinstance(op, ALLOWED_CMPS) for op in node.ops):
        # evaluate stepwise
        left = _eval(node.left, ctx)
        result = True
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, ctx)
            if isinstance(op, ast.Eq): ok = (left == right)
            elif isinstance(op, ast.NotEq): ok = (left != right)
            elif isinstance(op, ast.Lt): ok = (left < right)
            elif isinstance(op, ast.LtE): ok = (left <= right)
            elif isinstance(op, ast.Gt): ok = (left > right)
            elif isinstance(op, ast.GtE): ok = (left >= right)
            elif isinstance(op, ast.In): ok = (left in right)
            elif isinstance(op, ast.NotIn): ok = (left not in right)
            elif isinstance(op, ast.Is): ok = (left is right)
            elif isinstance(op, ast.IsNot): ok = (left is not right)
            else: ok = False
            if not ok:
                return False
            left = right
        return True

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_eval(elt, ctx) for elt in node.elts]

    if isinstance(node, ast.Dict):
        return {_eval(k, ctx): _eval(v, ctx) for k, v in zip(node.keys, node.values)}

    if isinstance(node, ast.Call):
        # allow safe builtins only
        if isinstance(node.func, ast.Name) and node.func.id in SAFE_BUILTINS:
            func = SAFE_BUILTINS[node.func.id]
            args = [_eval(a, ctx) for a in node.args]
            kwargs = {k.arg: _eval(k.value, ctx) for k in node.keywords}
            return func(*args, **kwargs)
        raise UnsafeExpression("Function calls are not allowed except safe builtins.")

    if isinstance(node, ast.Attribute):
        # allow attribute access for simple objects (dict-like; returns None if missing)
        value = _eval(node.value, ctx)
        try:
            return getattr(value, node.attr)
        except Exception:
            return None

    # Subscript (dict or list index)
    if isinstance(node, ast.Subscript):
        container = _eval(node.value, ctx)
        key = _eval(node.slice, ctx) if not isinstance(node.slice, ast.Index) else _eval(node.slice.value, ctx)
        try:
            return container[key]
        except Exception:
            return None

    # If we got here, node type not allowed
    raise UnsafeExpression(f"Unsupported expression node: {type(node).__name__}")

def eval_expr(expr: str, ctx: Dict[str, Any]) -> Any:
    tree = ast.parse(expr, mode="eval")
    # security: ensure only allowed nodes
    for n in ast.walk(tree):
        if not isinstance(n, ALLOWED_NODES):
            raise UnsafeExpression(f"Disallowed AST node: {type(n).__name__}")
    return _eval(tree, ctx)
