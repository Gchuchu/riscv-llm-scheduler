# riscv-llm-scheduler 性能评测

## 功能

benchmark 测试脚本，用于评测优化前后的推理性能。

### 测试场景
- 并发数：2、4、8
- 模型：Llama-3.2-1B/3B Q4_K_M
- 指标：TPS、平均 TTFT、P95 TTFT

### 使用方法

```bash
# TBD
python benchmark_llama.py --concurrency 2,4,8 --model model.gguf
```

## 当前状态

🚧 项目开发中
