# stress_test — stress-ng 分级负载压测

## 数据来源

| 项 | 说明 |
|---|---|
| 运行脚本 | [bench_runner_final.py](../../../src/benchmark/bench_runner_final.py)（与无负载对照实验同一脚本，仅场景配置不同） |
| 观测脚本 | [vstate_trace_llama_server.bt](../../../src/tracker/vstate_trace_llama_server.bt) |
| 采集时间 | `stressng_opt` 2026-09-25 12:12 – 09-26 19:58；`stressng_sched_opt` 09-26 20:32 – 09-27 04:40 |
| 运行环境 | Milk-V Jupiter（SpacemiT K1，8 核异构 X100/X60），openEuler riscv64 |

## 测试方法

在无负载对照实验的基础上叠加可控 CPU 压力，对比**有/无 sched_ext** 时推理进程受挤压的程度。

负载命令（每个 trial 独立起停）：

```bash
stress-ng --cpu 0 --cpu-load <P> --timeout 3600 --metrics-brief
```

| 参数 | 含义 |
|---|---|
| `--cpu 0` | 每个在线核起 1 个 worker（8 核 = 1 主进程 + 8 worker） |
| `--cpu-load <P>` | 每核占空比 P%（忙循环 + 休眠交替） |
| `--timeout 3600` | 兜底自杀开关，正常由运行器主动停止 |

关键约束：**负载在 llama-server 启动前就位**，整个 trial 期间保持，trial 结束后停止——即推理服务从冷启动到跑完都在压力环境下。

负载档位：20 / 40 / 60 / 80 / 100%（每核占空比）。

其余流程（清场 → 起 server → 挂 bpftrace → 50 并发请求 → 收数 → 收尾）和固定参数（模型 1B-Q4_K_M、`-t/-tb 4`、`-c 2048`、64 输出 token、128 token prompt、50 并发/轮、3 次重复）与无负载对照实验完全一致（见[上级 README](../README.md)）。

sched_ext 场景同样为场景级常驻（`vec_affine_sched` V3 在该场景 45 轮期间保持加载，不逐轮重启）。

## 场景矩阵

| 场景 | llama 构建 | 绑核 | sched_ext | 负载档 | 并发档 | 重复 | 轮数 |
|---|---|---|---|---|---|---|---|
| `stressng_opt` | 优化版 | 不绑 | 关 | 20/40/60/80/100% | 2/4/8 | 3 | 45 |
| `stressng_sched_opt` | 优化版 | 不绑 | **开** | 20/40/60/80/100% | 2/4/8 | 3 | 45 |

合计 **90 轮**。

## 目录与文件

每个场景目录 30 个 CSV = 5 档负载 × 3 并发档 × (汇总 + 明细)：

| 文件 | 内容 |
|---|---|
| `{场景}_load{P}_par{Q}_none.csv` | 轮次汇总，每 trial 一行（`load_pct` 列记录负载档位） |
| `{场景}_load{P}_par{Q}_none_detail.csv` | 逐请求明细，每请求一行 |

汇总 CSV 字段说明见[上级 README](../README.md#汇总-csv-字段)。

## 注意事项

- stress-ng 使用默认 `--cpu-method all`（未固定具体压力方法，worker 会在多种 CPU 压力方法间轮换）。stress-ng 日志会提示该模式下负载稳定性低于固定方法；**全矩阵使用同一参数，横向对比一致**。
- 100% 档下 llama-server 冷启动明显变慢，单轮墙钟时间也最长（无 sched_ext 时尤为明显）。
