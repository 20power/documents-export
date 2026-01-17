# -*- coding: utf-8 -*-
import io, time, json, yaml, requests
import numpy as np, pandas as pd
from pathlib import Path

from rules_engine import load_rules, apply_rules_on_row

def load_cfg(path="./config_stream.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main(cfg_path="./config_stream.yaml", rules_path="./rules.yaml"):
    cfg = load_cfg(cfg_path)
    url = cfg["service"]["url"]
    epsilon = cfg["service"]["epsilon"]
    inp = Path(cfg["io"]["input_csv"]).resolve()
    outp = Path(cfg["io"]["output_csv"]).resolve()
    outp.parent.mkdir(parents=True, exist_ok=True)

    rules = load_rules(rules_path) if Path(rules_path).exists() else None

    df = pd.read_csv(inp, encoding="utf-8")
    batch_size = int(cfg["batch"]["size"])
    sleep_ms = int(cfg["batch"]["sleep_ms"])

    outputs = []
    for i in range(0, len(df), batch_size):
        chunk = df.iloc[i:i+batch_size].copy()
        buff = io.BytesIO()
        chunk.to_csv(buff, index=False, encoding="utf-8")
        buff.seek(0)
        files = {"file": ("batch.csv", buff, "text/csv")}
        resp = requests.post(f"{url}?epsilon={epsilon}", files=files, timeout=60)
        resp.raise_for_status()
        res_df = pd.DataFrame(resp.json())
        merged = pd.concat([chunk.reset_index(drop=True), res_df.reset_index(drop=True)], axis=1)

        # 规则引擎：打标签（分项 over_* 与最终 level）
        if rules is not None:
            rule_outs = [apply_rules_on_row(rec, rules) for rec in merged.to_dict(orient="records")]
            rule_df = pd.DataFrame(rule_outs)
            merged = pd.concat([merged, rule_df], axis=1)
        else:
            # 后备：只做总额分级
            def fallback_level(r):
                thr = r.get("q95_total_adj", np.nan)
                fee = r.get("total_fee_sum", np.nan)
                if pd.isna(fee) or pd.isna(thr) or thr <= 0: 
                    return "NA"
                over = (fee - thr) / thr
                if over < 0.10: return "normal"
                if over < 0.30: return "yellow"
                if over < 0.50: return "orange"
                return "red"
            merged["level"] = merged.apply(fallback_level, axis=1)
            merged["reasons"] = ""

        outputs.append(merged)
        if sleep_ms > 0 and i + batch_size < len(df):
            time.sleep(sleep_ms / 1000.0)

    out_df = pd.concat(outputs, ignore_index=True) if outputs else df.copy()
    out_df.to_csv(outp, index=False, encoding="utf-8")
    print("Saved:", str(outp))

if __name__ == "__main__":
    main()
