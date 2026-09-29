# 测试结果

## 数据集

本目录同时保存 WSL 基线和真实 RVV 板卡的原始 benchmark 结果。实验参数、指标定义与 CSV 字段说明见[性能评测指南](../docs/benchmark/性能评测指南.md)。

### 真实 RVV 板卡数据

`rvv/` 目录包含自动化矩阵运行产生的完整 CSV。所有文件均保留 server 命令、模型路径、并发参数、context、prompt 和 V-state 字段，便于回溯。

| 实验 | 文件 | 内容 |
|---|---|---|
| 1B/3B/8B 模型规模，p4 | `rvv_experiment_138B.csv`、`rvv_experiment_138B_no_response.csv`、`rvv_experiment_138B_Summary.csv`、`rvv_experiment_138B_Summary_VState.csv` | 请求级原始结果、去响应正文版本、轮次汇总、V-state 汇总。 |
| 3B 并发扫描，第 1 次运行 | `rvv_experiment_3B_parallel.csv`、`rvv_experiment_3B_parallel_no_response.csv`、`rvv_experiment_3B_parallel_Summary.csv`、`rvv_experiment_3B_parallel_Summary_VState.csv` | p2/p4/p8/p16 的请求级结果与汇总。 |
| 3B 并发扫描，第 2 次运行 | `rvv_experiment_3B_parallel_2.csv`、`rvv_experiment_3B_parallel_2_no_response.csv`、`rvv_experiment_3B_parallel_2_Summary.csv`、`rvv_experiment_3B_parallel_2_Summary_VState.csv` | p2/p4/p8/p16 的第二次独立运行结果与汇总。 |

原始 CSV 包含模型响应正文；对应的 `_no_response` 文件仅移除 `response_text`，其余实验字段与数值保持一致，适合表格处理和绘图。

### WSL 基线

以下为 WSL x86 模拟环境数据，仅作功能验证和参考，不与真实 RVV 板卡性能混合统计。

| Concurrency | TPS (mean +/- std) | Avg TTFT (ms) | P99 TTFT (ms) |
|---|---:|---:|---:|
| 2 | 111.21 +/- 1.85 | 74.59 | 74.67 |
| 4 | 193.59 +/- 3.59 | 169.51 | 169.72 |
| 8 | 187.75 +/- 2.35 | 169.97 | 174.24 |

### sched_ext 调度策略对比

见 [sched_ext/README.md](sched_ext/README.md)，对比 CFS vs sched_ext 在不同时间片配置和负载条件下的推理性能。

### 最终实验数据集

`final_test/` 汇总两组对照实验：5 个无外部负载对照场景（`noopt_c0_3`、`noopt_c4_7`、`opt_nobind`、`sched_opt`、`sched_noopt`）+ `stress_test/`（stress-ng 分级负载 20–100%，内含 `stressng_opt`、`stressng_sched_opt`），共 135 轮 trial。完整说明见 [final_test/README.md](final_test/README.md)。
