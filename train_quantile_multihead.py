# -*- coding: utf-8 -*-
import os, json, yaml, joblib
import numpy as np, pandas as pd
from pathlib import Path
from typing import List, Tuple
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import GradientBoostingRegressor

def resolve_path(p: str, base_dir: Path) -> Path:
    pth = Path(p)
    return (base_dir / pth).resolve() if not pth.is_absolute() else pth

def load_cfg(cfg_path: str) -> tuple[dict, Path]:
    cfg_file = Path(cfg_path).resolve()
    with open(cfg_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f), cfg_file.parent

def read_table(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8")

def infer_feature_cols(df, id_cols, datetime_cols, wl_num, wl_cat):
    id_present = [c for c in id_cols if c in df.columns]
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in id_present]
    cat_cols = [c for c in df.columns if df[c].dtype == "object" and c not in id_present]
    if wl_num: num_cols = [c for c in wl_num if c in df.columns]
    if wl_cat: cat_cols = [c for c in wl_cat if c in df.columns]
    targets_like = [c for c in df.columns if str(c).startswith("total_fee_")]
    num_cols = [c for c in num_cols if c not in targets_like]
    return num_cols, cat_cols

def build_pipeline(num_cols, cat_cols):
    num_tf = Pipeline([("imputer", SimpleImputer(strategy="median"))])
    cat_tf = Pipeline([("imputer", SimpleImputer(strategy="most_frequent")),
                       ("ohe", OneHotEncoder(handle_unknown="ignore", min_frequency=0.005))])
    pre = ColumnTransformer([("num", num_tf, num_cols), ("cat", cat_tf, cat_cols)], remainder="drop", sparse_threshold=0.3)
    return Pipeline([("pre", pre), ("reg", GradientBoostingRegressor(loss="quantile", random_state=42))])

def fit_quantiles(pipe, X, y, quantiles):
    models = {}
    for q in quantiles:
        m = Pipeline([("pre", pipe.named_steps["pre"]), ("reg", GradientBoostingRegressor(loss="quantile", alpha=q, random_state=42))])
        m.fit(X, y)
        models[q] = m
    return models

def predict_quantiles(models, X):
    res = {}
    for q, m in models.items():
        res[f"q{int(q*100)}"] = m.predict(X)
    return pd.DataFrame(res)

def coverage_rate(y_true, q_pred):
    return float(np.mean(y_true <= q_pred))

def clamp_total(total_q, parts_q50, parts_q95, eps):
    lo = parts_q50 - eps
    hi = parts_q95 + eps
    return float(np.clip(total_q, lo, hi))

def main(cfg_path="./config_qr.yaml"):
    cfg, cfg_dir = load_cfg(cfg_path)
    # 解析所有路径为绝对路径（基于配置文件所在目录）
    train_csv = resolve_path(cfg["data"]["train_csv"], cfg_dir)
    test_csv  = resolve_path(cfg["data"]["test_csv"], cfg_dir)
    out_dir   = resolve_path(cfg["output"]["dir"], cfg_dir)
    models_dir= out_dir / cfg["output"]["models_dir"]
    out_dir.mkdir(parents=True, exist_ok=True); models_dir.mkdir(parents=True, exist_ok=True)

    train_df = read_table(train_csv)

    num_cols, cat_cols = infer_feature_cols(
        train_df, cfg["data"].get("id_cols", []), cfg["data"].get("datetime_cols", []),
        cfg["data"].get("numeric_features_whitelist", []), cfg["data"].get("categorical_features_whitelist", [])
    )
    per_heads = cfg["targets"]["per_head"]
    total_head = cfg["targets"]["total_head"]
    quantiles = cfg["quantiles"]

    train_df = train_df.dropna(subset=per_heads + [total_head]).copy()
    X = train_df[num_cols + cat_cols]

    base_pipe = build_pipeline(num_cols, cat_cols)

    # 分头
    reports = []
    per_concat_list = []
    for head in per_heads:
        y = train_df[head].astype(float)
        models = fit_quantiles(base_pipe, X, y, quantiles)
        for q, m in models.items():
            joblib.dump(m, models_dir / f"qr_{head}_q{int(q*100)}.joblib")
        pred = predict_quantiles(models, X)
        pred.columns = [f"{head}__{c}" for c in pred.columns]
        per_concat_list.append(pred)
        if 0.95 in models:
            reports.append({"target": head, "metric": "coverage@q95", "value": coverage_rate(y.values, pred[f"{head}__q95"].values)})

    per_concat = pd.concat(per_concat_list, axis=1)

    # 总额
    y_total = train_df[total_head].astype(float)
    tot_models = fit_quantiles(base_pipe, X, y_total, quantiles)
    for q, m in tot_models.items():
        joblib.dump(m, models_dir / f"qr_{total_head}_q{int(q*100)}.joblib")
    tot_pred = predict_quantiles(tot_models, X)
    if 0.95 in tot_models:
        reports.append({"target": total_head, "metric": "coverage@q95", "value": coverage_rate(y_total.values, tot_pred["q95"].values)})

    # 一致性校准
    eps = float(cfg["calibration"].get("epsilon", 100.0))
    parts_q50_sum = per_concat.filter(like="__q50").sum(axis=1).values
    parts_q95_sum = per_concat.filter(like="__q95").sum(axis=1).values
    total_q95_raw = tot_pred["q95"].values
    total_q95_adj = [clamp_total(tq, p50, p95, eps) for tq, p50, p95 in zip(total_q95_raw, parts_q50_sum, parts_q95_sum)]
    reports.append({"target": total_head, "metric": "coverage@q95_adj", "value": coverage_rate(y_total.values, np.array(total_q95_adj))})

    # 导出报告 & 预测快照
    rep_df = pd.DataFrame(reports)
    rep_df.to_csv(out_dir / cfg["output"]["report_csv"], index=False, encoding="utf-8")
    dump = pd.DataFrame({
        "y_total": y_total.values,
        "q95_total_raw": total_q95_raw,
        "q95_total_adj": total_q95_adj,
        "parts_q50_sum": parts_q50_sum,
        "parts_q95_sum": parts_q95_sum,
    })
    dump = pd.concat([dump, per_concat], axis=1)
    dump.to_csv(out_dir / "train_in_sample_preds.csv", index=False, encoding="utf-8")

    # 元数据（相对路径）
    meta = {
        "num_features": num_cols,
        "cat_features": cat_cols,
        "per_heads": per_heads,
        "total_head": total_head,
        "quantiles": quantiles,
        "models_dir": str(Path(cfg["output"]["dir"]) / cfg["output"]["models_dir"]),  # 相对 config 的路径
        "report_csv": str(Path(cfg["output"]["dir"]) / cfg["output"]["report_csv"]),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OK: trained.")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=str, default="./config_qr.yaml")
    args = ap.parse_args()
    main(args.config)
