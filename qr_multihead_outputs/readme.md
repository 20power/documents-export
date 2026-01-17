
## 文件说明

### `meta.json`

这个 JSON 文件包含了关于项目配置的关键元数据。它定义了：
- `num_features`: 用于训练的数值特征列表。
- `cat_features`: 用于训练的类别特征列表。
- `per_heads`: 需要分别预测的费用项目（例如 `total_fee_yiliao`, `total_fee_huli`）。
- `total_head`: 总费用预测的目标（例如 `total_fee_sum`）。
- `quantiles`: 模型训练时所使用的分位数（例如 0.5, 0.9, 0.95, 0.99）。
- `models_dir`: 指向存储训练模型的目录的相对路径 (`models/`)。
- `report_csv`: 指向模型评估报告的相对路径 (`qr_report.csv`)。

### `models/`

此目录以 `.joblib` 文件的形式存储了所有训练好的机器学习模型。文件名遵循 `qr_{TARGET}_{QUANTILE}.joblib` 的约定：
- `{TARGET}`: 正在预测的具体费用类型（例如 `total_fee_sum`）。
- `{QUANTILE}`: 预测的分位数水平（例如 `q95` 代表第95个百分位数）。

这些模型由 `threshold_service_fastapi.py` 加载以进行预测。

### `qr_report.csv`

此 CSV 文件包含了模型的评估指标。它通常包括像 `coverage@q95` 这样的指标，该指标衡量实际值落在预测的95%分位数阈值以下的频率。这有助于评估模型的性能和可靠性。

### `train_in_sample_preds.csv`

此文件包含模型在原始训练数据集上的预测结果，可用于样本内性能分析和调试。

### `infer/`

此子目录存放批量推理运行的结果。
- `inference_with_thresholds.csv`: 由预测脚本（如 `infer_quantile_multihead.py`）或 FastAPI 服务生成的输出。它包含输入特征以及每个费用项目的预测分位数阈值。

### `stream/`

此子目录包含与实时流处理相关的输出。
- `alerts.csv`: 可能包含基于预定义规则触发警报的事件记录，这些记录可能由 Flink 作业（`flink_job_pyflink_rules.py`）或 `stream_runner_minimal.py` 脚本生成。
