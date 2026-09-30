# final_test — 最终实验数据集

汇总两组对照实验，共 **6 个场景目录**：5 个无外部负载对照 + `stress_test/`（内含 2 个分级负载场景），合计 135 轮 trial。

## 数据来源

| 项 | 说明 |
|---|---|
| 运行脚本 | [bench_runner_final.py](../../src/benchmark/bench_runner_final.py)（最终版自动化运行器，宿主机运行，SSH 下发到板卡） |
| 观测脚本 | [vstate_trace_llama_server.bt](../../src/tracker/vstate_trace_llama_server.bt)（bpftrace，挂 `sched:sched_switch` 统计 V 状态 save/restore，挂 `kprobe:finish_task_switch` 计切换耗时） |
| 采集时间 | 无负载对照：2026-09-17 17:06 – 09-18 02:07；分级负载：2026-09-25 12:12 – 09-27 04:40 |
| 运行环境 | Milk-V Jupiter（SpacemiT K1，8 核异构 X100/X60），openEuler riscv64 |

## 测试方法（所有场景共用）

每个 trial 流程固定：清场 → [起 stress 负载] → 起 llama-server → 挂 bpftrace → 发送 50 个并发请求 → 收数据 → 收尾。

固定实验参数：

| 参数 | 值 |
|---|---|
| 模型 | Llama-3.2-1B-Instruct-Q4_K_M |
| 推理线程 | `-t/-tb 4` |
| 上下文 | `-c 2048` |
| 输出上限 | 64 token / 请求 |
| Prompt | 固定 128 token 模板 |
| 并发请求 | 50 / 轮 |
| 并发档（`--parallel`） | 2 / 4 / 8 |
| 重复次数 | 每档 3 次 trial |

sched_ext 场景统一使用 `vec_affine_sched` V3，且为**场景级常驻**：场景开始时加载一次，跑完该场景全部 trial 才停止，不在 trial 之间反复切换。

## 目录一览（5 + 1）

| 目录 | 实验组 | llama 构建 | 绑核 | sched_ext | 外部负载 | 轮数 |
|---|---|---|---|---|---|---|
| `noopt_c0_3/` | 无负载对照 | 普通版（纯通用 RVV） | `-Cr 0-3` | 关 | 无 | 9 |
| `noopt_c4_7/` | 无负载对照 | 普通版（纯通用 RVV） | `-Cr 4-7` | 关 | 无 | 9 |
| `opt_nobind/` | 无负载对照 | 优化版（xsmtvdot IME 内核 + AI 核偏好） | 不绑 | 关 | 无 | 9 |
| `sched_opt/` | 无负载对照 | 优化版 | 不绑 | **开** | 无 | 9 |
| `sched_noopt/` | 无负载对照 | 普通版 | 不绑 | **开** | 无 | 9 |
| `stress_test/` | 分级负载压测 | 优化版 | 不绑 | 关 / 开（两个场景） | stress-ng 20–100% | 90 |

每个目录 = 一个场景；每轮 = 一次 trial（3 并发档 × 3 次重复）。

### 1）无外部负载对照（5 个目录）

不施加任何外部负载，用于分离三个因素的影响：**绑核**（`noopt_c0_3` vs `noopt_c4_7`）、**llama 构建版本**（`opt_nobind` vs `noopt_*`）、**sched_ext**（`sched_opt` vs `opt_nobind`、`sched_noopt` vs 普通版）。

### 2）stress-ng 分级负载压测（`stress_test/`）

在上述方法上叠加可控 CPU 压力，负载命令：

```bash
stress-ng --cpu 0 --cpu-load <P> --timeout 3600 --metrics-brief
```

`--cpu 0` = 每个在线核 1 个 worker（8 核 = 1 主进程 + 8 worker）；`--cpu-load <P>` = 每核占空比 P%。**负载在 llama-server 启动前就位**，整个 trial 期间保持，trial 结束后停止。

含两个场景：`stressng_opt`（无 sched_ext）与 `stressng_sched_opt`（开 sched_ext），各 5 档负载 × 3 并发档 × 3 次 = 45 轮。详见 [stress_test/README.md](stress_test/README.md)。

## 文件结构

每个场景目录内为「汇总 + 明细」成对 CSV：

| 文件 | 内容 |
|---|---|
| `{场景}_par{P}_{method}.csv`（无负载对照；`-Cr` 场景为 `_cr`，其余为 `_none`）/ `{场景}_load{P}_par{Q}_none.csv`（分级负载） | 轮次汇总，每 trial 一行 |
| 同名 `_detail.csv` | 逐请求明细，每请求一行（`ttft_ms` / `tps` / `tokens` / `e2e_ms`） |

## 汇总 CSV 字段

| 组 | 字段 | 含义 |
|---|---|---|
| 吞吐/延迟 | `wall_s`、`agg_tps`、`avg_ttft_ms`、`p95_ttft_ms`、`avg_tps`、`total_tokens` | 整轮墙钟时间、聚合吞吐、平均/P95 首 token 延迟 |
| V 状态 | `v_saves`、`v_restores`、`target_saves`、`nontarget_saves` | V 状态 save/restore 总数；目标核 / 非目标核上的 save（后者 >0 即绑核泄漏） |
| 切换耗时 | `v_switch_{count,avg_ns,total_ns}`、`nonv_switch_{count,avg_ns,total_ns}` | V 状态切换与非 V 状态切换的耗时统计 |
| per-CPU | `cpu0_saves` … `cpu7_restores` | 每个核上的 save/restore 次数 |
| 分布（JSON） | `cpu_saves_by_proc`、`cpu_restores_by_proc`、`saves_by_proc`、`restores_by_proc`、`tid_cpu_saves`、`tid_cpu_restores` | 按进程 / 线程维度的核间分布 |
| 负载档 | `load_pct` | 该轮使用的 stress-ng 占空比（无负载对照场景为 0） |

## 注意事项

- stress-ng 使用默认 `--cpu-method all`（未固定具体压力方法，worker 会在多种 CPU 压力方法间轮换）。stress-ng 日志会提示该模式下负载稳定性低于固定方法；**全矩阵使用同一参数，横向对比一致**。
- 100% 负载档下 llama-server 冷启动明显变慢，单轮墙钟时间也最长（无 sched_ext 时尤为明显）。
