# -*- coding: utf-8 -*-
import yaml
import numpy as np
from pathlib import Path
from typing import Dict, Any
from rules_dsl import eval_expr

DEFAULT_HEADS = [
    "total_fee_yiliao",
    "total_fee_huli",
    "total_fee_wugong",
    "total_fee_yiyang",
    "total_fee_houxu",
    "total_fee_other",
    "total_fee_jingshen",
]

def load_rules(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def label_by_bands(over: float, bands: Dict[str, list[str]]) -> str:
    if over < bands["yellow"][0]:
        return "normal"
    if over < bands["yellow"][1]:
        return "yellow"
    if over < bands["orange"][1]:
        return "orange"
    return "red"

def compute_over_levels(row, bands, heads=DEFAULT_HEADS) -> Dict[str, str]:
    res = {}
    for h in heads:
        fee = row.get(h, np.nan)
        # 模型返回的 p95 列名： f"{h}__q95"
        thr = row.get(f"{h}__q95", np.nan)
        if (fee is None) or (thr is None) or (thr <= 0) or (str(fee) == "nan") or (str(thr) == "nan"):
            res[f"over_{h.split('_')[-1]}"] = "NA"
            continue
        over = (fee - thr) / thr
        res[f"over_{h.split('_')[-1]}"] = label_by_bands(over, bands)
    return res

def apply_rules_on_row(row: dict, rules: Dict[str, Any]) -> Dict[str, Any]:
    bands = rules.get("bands", {"yellow":[0.1,0.3], "orange":[0.3,0.5], "red":[0.5, 9.99]})
    out = {"reasons": []}

    # 1) 分项超额标签（用于 combine）
    over_flags = compute_over_levels(row, bands)
    out.update(over_flags)

    # 2) hard_rules 命中即红
    hard_hit = False
    for rule in rules.get("hard_rules", []):
        name = rule.get("name", "hard")
        expr = rule.get("expr", "")
        if expr:
            try:
                ok = bool(eval_expr(expr, row))
            except Exception:
                ok = False
            if ok:
                hard_hit = True
                out["reasons"].append(f"HARD:{name}")
    level = "red" if hard_hit else "normal"

    # 3) combine 组合升级
    for rule in rules.get("combine", []):
        name = rule.get("name", "combine")
        expr = rule.get("expr", "")
        target = rule.get("level", "red")
        if expr:
            try:
                ok = bool(eval_expr(expr, out | row))  # 允许在表达式里使用 over_* 标签
            except Exception:
                ok = False
            if ok:
                level = target
                out["reasons"].append(f"COMBINE:{name}->{target}")

    out["level"] = level
    return out
