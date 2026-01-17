
# 人伤智能估损（端到端复现指南）

> 面向“车险/意外险人身伤害理赔”，提供**分费用多头分位数阈值** + **总额一致性校准** + **规则驱动分级预警**的端到端实现，覆盖：数据准备 → 训练/评估 → 阈值推理服务 → 实时/批量 → 规则体系 → 容器化/Helm → Airflow 编排。  
> 甲方只需按本文档准备数据与环境，即可**完整复现**并按需**弹性部署**。

---

## 0. 目录结构与组件

```
.
├─ config_qr.yaml                      # 训练/推理配置
├─ train_with_totals.csv               # 训练集（含分项总费用 & total_fee_sum）
├─ test_with_totals.csv                # 测试集（同口径）
├─ train_quantile_multihead.py         # 训练：多头分位数回归（q50/90/95/99）
├─ infer_quantile_multihead.py         # 推理：输出阈值 + 示例分级
├─ threshold_service_fastapi.py        # FastAPI 阈值服务（POST CSV -> 阈值JSON）
├─ service_ext.py                      # 扩展路由（/health, /meta）
├─ config_stream.yaml                  # 流式Runner配置
├─ stream_runner_minimal.py            # 最小HTTP批量Runner（含规则引擎）
├─ flink_job_pyflink_example.py        # PyFlink骨架（HTTP批，演示）
├─ flink_job_pyflink_rules.py          # PyFlink骨架（HTTP批+规则）
├─ rules.yaml                          # 规则配置（bands/hard/combine）
├─ rules_dsl.py                        # 规则表达式安全解析（AST白名单）
├─ rules_engine.py                     # 规则引擎：分项over_*与最终level
├─ docker-compose.yml                  # 容器化启动（本地）
├─ Dockerfile                          # 服务镜像（含/health和/meta）
├─ requirements.txt                    # 全量依赖（可复现训练/服务/流式/可选GAN）
├─ requirements-service.txt            # 轻量服务依赖（仅部署服务时）
├─ helm/threshold-service/*            # Helm Chart（Ingress/HPA/Prometheus）
├─ airflow/dags/threshold_pipeline_dag.py  # Airflow 编排：训练→推理→归档
├─ shap_report.py                      # 可解释性（SHAP）报告（可选）
├─ gan_assist_threshold.py             # GAN 尾部分布建模原型（可选）
├─ gan_sample_assist.py                # 基于GAN采样的q95_total估计（可选）
├─ metrics_stub.py                     # /metrics 简易导出（Prometheus，示例）
├─ demo_events.jsonl                   # Flink本地演示输入
└─ qr_multihead_outputs/               # 训练/推理产出（脚本运行后生成）
   ├─ models/                          # 各head各分位点模型（joblib）
   ├─ meta.json                        # 元数据（特征列、目标头、分位点…）
   ├─ qr_report.csv                    # 训练评估报告（覆盖率等）
   ├─ train_in_sample_preds.csv        # 训练内快照（可选）
   └─ infer/inference_with_thresholds.csv
```

---

## 1. 环境准备（强烈建议在干净虚拟环境中）

### 方式A：一次性安装全量依赖（推荐）
```bash
pip install -r requirements.txt
```

### 方式B：仅部署服务（轻量）
```bash
pip install -r requirements-service.txt
```

> **Python 3.9+** 建议。`apache-flink`、`torch` 属可选，大型依赖若安装不便，请按需注释或采用容器化。

---

## 2. 数据准备（关键字段口径）

1) **分项总费用列**（示例名与任务书一致，可按你司口径映射）：  
`total_fee_yiliao, total_fee_huli, total_fee_wugong, total_fee_yiyang, total_fee_houxu, total_fee_other, total_fee_jingshen`  
> 对应“单项费用 × 项数”的聚合：如 `total_fee_huli = unit_fee_huli × quantity_huli`。

2) **总额列**：`total_fee_sum` = 各分项之和（保持与业务口径一致）。

3) **特征列**：
- 数值特征（如：三期天数、住院天数、年龄、历史赔付强度、地区均薪等）
- 类别特征（如：地区、职业、治疗方式、伤情等级等）
- （可选）诊断文本：当前版本**不强依赖**，可留待后续NLP增强。

4) 导出为 CSV：`train_with_totals.csv`、`test_with_totals.csv`（UTF-8，无货币符号）。

---

## 3. 训练与评估

### 3.1 配置（`config_qr.yaml`）要点
- `targets.per_head`：纳入建模的分项列名（如新增/删减需同步修改）。
- `targets.total_head`：总额列名（必须与口径一致）。
- `quantiles`：默认 `[0.5, 0.9, 0.95, 0.99]`。
- `calibration.epsilon`：总额p95的“夹逼”松弛（元）。

### 3.2 启动训练
```bash
python train_quantile_multihead.py --config ./config_qr.yaml
```
产出：
- `./qr_multihead_outputs/models/qr_<head>_q{50,90,95,99}.joblib`
- `./qr_multihead_outputs/meta.json`
- `./qr_multihead_outputs/qr_report.csv`（含 `coverage@q95`）

> KPI 建议：各分群 `coverage@q95` 稳定在 **0.93–0.97**。若偏差大，建议启用**分群建模**（地区×治疗方式×严重度×职业等）。

### 3.3 离线推理与核验
```bash
python infer_quantile_multihead.py
```
产出：`./qr_multihead_outputs/infer/inference_with_thresholds.csv`  
包含每条样本的**分项q50/q90/q95/q99**与**总额q95（raw/adj）**，可与专家审核对比，目标**准确率≥80%**。

---

## 4. 阈值服务（FastAPI）

### 4.1 启动（默认读取 `./qr_multihead_outputs/meta.json`）
```bash
uvicorn service_ext:app --host 0.0.0.0 --port 8000
```
> 如需显式指定：`META_PATH=./qr_multihead_outputs/meta.json uvicorn service_ext:app --host 0.0.0.0 --port 8000`

### 4.2 健康与元数据
- `GET /health`：心跳+META存在性
- `GET /meta`：返回当前模型元数据（特征列、目标头、分位点等）

### 4.3 预测接口
- 路由：`POST /predict_thresholds?epsilon=100.0`
- 请求：`multipart/form-data`，字段名 `file`（CSV）
- 返回：各分项分位点阈值列（`<head>__q50/q90/q95/q99`）+ `q95_total_raw` + `q95_total_adj`

**curl 示例：**
```bash
curl -F "file=@./test_with_totals.csv" "http://127.0.0.1:8000/predict_thresholds?epsilon=100.0"
```

---

## 5. 规则体系与分级预警

### 5.1 规则配置（`rules.yaml`）
- `bands`：分项**超额比例**到 `normal/yellow/orange/red` 的区间定义  
- `hard_rules`：命中即红（表达式在行上下文求值）  
- `combine`：基于分项标签（`over_*`）的**组合升级**

### 5.2 规则引擎（`rules_engine.py`）输出
- `over_yiliao / over_huli / ...`：分项标签（normal/yellow/orange/red/NA）
- `level`：最终分级（含 hard 与 combine 升级）
- `reasons`：命中原因（审计友好）

> 表达式由 `rules_dsl.py` **安全解析**（AST 白名单）。

---

## 6. 批量/流式处理

### 6.1 最小可跑Runner（无需Flink）
配置：`config_stream.yaml`，执行：
```bash
python stream_runner_minimal.py
```
流程：从 `input_csv` 读入 → **按批调用**服务 → 合并阈值 → **应用规则** → 输出 `output_csv`。

### 6.2 PyFlink 作业骨架（生产建议）
- `flink_job_pyflink_rules.py`：HTTP 批 + 规则引擎（推荐）  
- 将 `read_text_file("./demo_events.jsonl")` 替换为 **Kafka Source**；Sink 改为 **Kafka/ES**，即可投入实时链路。

---

## 7. 运维与部署

### 7.1 容器化（本地）
```bash
docker compose up --build -d
curl http://127.0.0.1:8000/health
```

### 7.2 Helm（K8s）
1) 配置镜像与路径：`helm/threshold-service/values.yaml`  
2) 可选启用 Ingress/HPA/Prometheus 注解  
3) 部署：
```bash
helm install inj-qr ./helm/threshold-service
```

### 7.3 Airflow（编排）
把 `airflow/dags/threshold_pipeline_dag.py` 放入 Airflow 的 `dags/`，在 UI 依次运行：
- `train_qr_multihead` → `offline_infer_snapshot` → `package_and_archive`  
归档压缩包会生成在 `./artifacts/qr_models_*.zip`。

---

## 8. 可解释性与尾部分布（可选）

- `shap_report.py`：生成每个 head/分位点的 SHAP 条形图（Top20），帮助定位重要特征。
- `gan_assist_threshold.py` + `gan_sample_assist.py`：学习**分项向量的条件分布**，基于采样给出 `gan_q95_total`，与 `q95_total_adj` 融合以提高**极端长尾**场景的敏感度。

---

## 9. 典型问题 & 排障

- **接口500/找不到模型**：确认 `qr_multihead_outputs/meta.json` 存在；或用 `META_PATH=...` 指定。
- **上传CSV报缺列**：以 `meta.json` 中 `num_features` + `cat_features` 为准，确保拼写与类型一致。
- **coverage@q95 偏差大**：增强特征、做分群训练、调 `epsilon` 或引入 `gan_q95_total` 作为上界参考。
- **性能**：HTTP 批量上传可显著降开销；Uvicorn 进程数可调；K8s 开启 HPA。
- **规则版本化**：建议将 `rules.yaml` 纳入仓库并加审批流（PR/MR）；线上变更采用灰度发布。

---

## 10. 合规与安全提示

- 仅对**脱敏**数据训练与推理；敏感字段请在进入本系统前清洗/替换。
- 规则表达式引擎为**只读**安全子集；但上线前仍应进行安全审计与渗透测试。
- 保留审计日志（建议在 Runner/Flink 中记录 `level/reasons`）。

---

## 11. 快速复现清单（甲方最小路径）

1) 准备两份 CSV：`train_with_totals.csv`、`test_with_totals.csv`（含分项与 `total_fee_sum`、必要特征）。  
2) 安装依赖：`pip install -r requirements.txt`  
3) 训练：`python train_quantile_multihead.py --config ./config_qr.yaml`  
4) 启服务：`uvicorn service_ext:app --host 0.0.0.0 --port 8000`  
5) 模拟调用：`python stream_runner_minimal.py`（产出 `./qr_multihead_outputs/stream/alerts.csv`）  
6) （可选）容器化/Helm/Airflow：按 7 章指引部署与编排。

---

## 12. 版本说明

- **v1**：训练/服务/Runner/规则/README 基础版  
- **v2**：Docker/Helm/Airflow/SHAP/GAN 原型补充  
- **v3**：`/health`、`/meta`、Helm Ingress/HPA/Prom 注解、Airflow 归档Task

> 建议按“月”为周期做覆盖率/误报回顾，结合错案分析迭代特征与规则。

---

## 13. 许可证与免责声明

本项目为方法与工程实践参考，需结合甲方制度、口径、合规策略进行二次校验与审批后上线。涉及医疗/个人信息时，请遵守当地法律法规与公司内控要求。
