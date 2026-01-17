# -*- coding: utf-8 -*-
# PyFlink 骨架（示意）。实际运行需在可用的 Flink 环境中执行。
from pyflink.common import Types
from pyflink.datastream import StreamExecutionEnvironment, RuntimeExecutionMode
from pyflink.datastream.connectors.kafka import KafkaSource, KafkaOffsetsInitializer, KafkaSink, DeliveryGuarantee
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream.functions import RichFlatMapFunction
from pyflink.datastream import DataStream
import io, json, yaml, requests, pandas as pd

CFG_PATH = "./config_stream.yaml"

def load_cfg(path=CFG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

class BatchHTTP(RichFlatMapFunction):
    def open(self, ctx):
        cfg = load_cfg()
        self.url = cfg["service"]["url"]
        self.epsilon = cfg["service"]["epsilon"]
        self.batch_size = int(cfg["batch"]["size"])
        self.buf = []

    def close(self):
        for out in self._flush():
            yield out

    def _flush(self):
        if not self.buf:
            return []
        df = pd.DataFrame(self.buf)
        buff = io.BytesIO()
        df.to_csv(buff, index=False, encoding="utf-8")
        buff.seek(0)
        files = {"file": ("batch.csv", buff, "text/csv")}
        r = requests.post(f"{self.url}?epsilon={self.epsilon}", files=files, timeout=60)
        r.raise_for_status()
        preds = r.json()
        outs = []
        for rec, pred in zip(self.buf, preds):
            merged = dict(rec); merged.update(pred)
            outs.append(json.dumps(merged, ensure_ascii=False))
        self.buf.clear()
        return outs

    def flat_map(self, value):
        rec = json.loads(value)
        self.buf.append(rec)
        if len(self.buf) >= self.batch_size:
            for out in self._flush():
                yield out

def main():
    cfg = load_cfg()
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_runtime_mode(RuntimeExecutionMode.STREAMING)
    env.set_parallelism(1)

    # Source 示例（Kafka）。实际替换 brokers/topic 等参数
    # source = KafkaSource.builder() \
    #     .set_bootstrap_servers("localhost:9092") \
    #     .set_topics("claims-events") \
    #     .set_group_id("threshold-job") \
    #     .set_starting_offsets(KafkaOffsetsInitializer.latest()) \
    #     .set_value_only_deserializer(SimpleStringSchema()) \
    #     .build()
    # ds = env.from_source(source, watermark_strategy=None, source_name="kafka-source")

    # 演示：从本地 JSONL 文件读（每行一条 JSON）
    ds = env.read_text_file("./demo_events.jsonl")

    ds2 = ds.flat_map(BatchHTTP(), output_type=Types.STRING())

    # Sink 示例（Kafka）
    # sink = KafkaSink.builder() \
    #     .set_bootstrap_servers("localhost:9092") \
    #     .set_record_serializer(
    #         KafkaRecordSerializationSchema.builder()
    #         .set_topic("claims-alerts")
    #         .set_value_serialization_schema(SimpleStringSchema())
    #         .build()
    #     ).set_delivery_guarantee(DeliveryGuarantee.AT_LEAST_ONCE).build()
    # ds2.sink_to(sink)

    # 演示：写到本地文件
    ds2.write_as_text("./qr_multihead_outputs/stream/flink_out.jsonl")
    env.execute("threshold-flink-job")

if __name__ == "__main__":
    main()
