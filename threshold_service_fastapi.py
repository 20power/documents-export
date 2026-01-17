# -*- coding: utf-8 -*-
"""
FastAPI 阈值服务（相对路径版）
启动： uvicorn threshold_service_fastapi:app --host 0.0.0.0 --port 8000
环境变量：
  META_PATH=./qr_multihead_outputs/meta.json   # 可覆盖默认路径
"""
import io, os, json, joblib, numpy as np, pandas as pd
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Query

app = FastAPI(title="Injury-Claims Threshold Service", version="0.1.0")

def resolve_path(p: str, base_dir: Path) -> Path:
    pth = Path(p)
    return (base_dir / pth).resolve() if not pth.is_absolute() else pth

BASE_DIR = Path(".").resolve()
META_PATH = resolve_path(os.getenv("META_PATH", "./qr_multihead_outputs/meta.json"), BASE_DIR)

def load_meta(meta_path: Path) -> dict:
    return json.loads(meta_path.read_text(encoding="utf-8"))

META = load_meta(META_PATH)
MODELS_DIR = resolve_path(META["models_dir"], BASE_DIR)

def load_models(models_dir: Path, targets, quantiles):
    models = {}
    for t in targets:
        models[t] = {}
        for q in quantiles:
            f = models_dir / f"qr_{t}_q{int(q*100)}.joblib"
            models[t][q] = joblib.load(f)
    return models

PER_MODELS = load_models(MODELS_DIR, META["per_heads"], META["quantiles"])
TOT_MODELS = load_models(MODELS_DIR, [META["total_head"]], META["quantiles"])[META["total_head"]]

def predict_quantiles(models_q, X: pd.DataFrame) -> pd.DataFrame:
    res = {}
    for q, m in models_q.items():
        res[f"q{int(q*100)}"] = m.predict(X)
    return pd.DataFrame(res)

def clamp_total(total_q95_raw, parts_q50_sum, parts_q95_sum, eps):
    lo = parts_q50_sum - eps
    hi = parts_q95_sum + eps
    return np.clip(total_q95_raw, lo, hi)

@app.post("/predict_thresholds")
async def predict_thresholds(file: UploadFile = File(...), epsilon: float = Query(100.0)):
    df = pd.read_csv(io.BytesIO(await file.read()), encoding="utf-8")
    X = df[META["num_features"] + META["cat_features"]]

    per_frames = []
    for head in META["per_heads"]:
        pred = predict_quantiles(PER_MODELS[head], X)
        pred.columns = [f"{head}__{c}" for c in pred.columns]
        per_frames.append(pred)
    per_concat = pd.concat(per_frames, axis=1)

    tot_pred = predict_quantiles(TOT_MODELS, X)
    parts_q50_sum = per_concat.filter(like="__q50").sum(axis=1).values
    parts_q95_sum = per_concat.filter(like="__q95").sum(axis=1).values
    total_q95_raw = tot_pred["q95"].values
    total_q95_adj = clamp_total(total_q95_raw, parts_q50_sum, parts_q95_sum, float(epsilon))

    out_df = pd.concat([per_concat, tot_pred], axis=1)
    out_df["q95_total_raw"] = total_q95_raw
    out_df["q95_total_adj"] = total_q95_adj
    return json.loads(out_df.to_json(orient="records"))
