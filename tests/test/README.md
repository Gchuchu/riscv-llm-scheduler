# 测试结果

本目录包含 `bench_runner.py` 自动化测试脚本在 openEuler RISC-V 板上执行的全部实验结果。

## 测试方法

每组测试参数见 `bench_runner_all.py` 中的 `GROUPS` 列表。每组测试执行 6 种绑核/调度方法（taskset、cgroup cpuset、partition-root、systemd-scope、SCHED_BATCH、SCHED_FIFO），每种方法重复 3 次 trial。每次 trial 发送 50 个并发 HTTP 请求，bpftrace 在板子后台采集 V-state save/restore 数据。

## 目录结构

每组一个子目录（`group1/` ~ `group10/`），每个子目录包含：

| 文件 | 内容 |
|------|------|
| `<method>.csv` | 汇总数据，每 trial 一行（TPS/TTFT/P95/V-saves/Per-CPU） |
| `<method>_detail.csv` | 每请求明细，含 TTFT/TPS/E2E (50 请求 × 3 trial = 150 行) |

## CSV 字段说明

### 汇总 CSV

| 字段 | 含义 |
|------|------|
| `trial` | 第几次 trial (1-3) |
| `method` | 绑核方法名称 |
| `wall_s` | 本轮推理挂钟时间（秒） |
| `agg_tps` | 总吞吐（总 token / wall time） |
| `avg_ttft_ms` | 平均首 token 延迟（毫秒） |
| `p95_ttft_ms` | 95 分位首 token 延迟 |
| `avg_tps` | 平均单请求 TPS |
| `total_tokens` | 本轮总生成 token 数 |
| `v_saves` | V-state 保存总次数 |
| `v_restores` | V-state 恢复总次数 |
| `target_saves` | 目标核心上的 save 次数 |
| `nontarget_saves` | 非目标核心上的 save 次数（期望为 0） |
| `cpu0_saves` ~ `cpu7_saves` | 每个 CPU 核心上的 save 次数 |

### 明细 CSV

| 字段 | 含义 |
|------|------|
| `method` | 绑核方法 |
| `trial` | 第几次 trial |
| `req` | 请求序号 (0-49) |
| `ttft_ms` | 该请求的首 token 延迟 |
| `tps` | 该请求的吞吐（tok/s） |
| `tokens` | 该请求生成的 token 数 |
| `e2e_ms` | 该请求的端到端延迟 |

## 十组测试参数速查

| 组 | 模型 | t | 核 | prompt | 输出 | ctx | par |
|----|------|---|-----|--------|------|-----|-----|
| 1 | 1B | 1 | 1 (7) | 64 | 32 | 1024 | 2 |
| 2 | 1B | 2 | 2 (7,6) | 64 | 32 | 1024 | 2 |
| 3 | 3B | 4 | 4 (4-7) | 128 | 64 | 2048 | 2 |
| 4 | 3B | 4 | 4 (4-7) | 128 | 32 | 2048 | 4 |
| 5 | 3B | 4 | 4 (4-7) | 64 | 64 | 2048 | 4 |
| 6 | 3B | 4 | 4 (4-7) | 128 | 64 | 2048 | 4 |
| 7 | 3B | 4 | 4 (4-7) | 256 | 64 | 2048 | 4 |
| 8 | 3B | 4 | 4 (4-7) | 128 | 128 | 2048 | 4 |
| 9 | 3B | 4 | 4 (4-7) | 128 | 64 | 2048 | 8 |
| 10 | 3B | 4 | 4 (4-7) | 256 | 256 | 4096 | 4 |

## 说明

- 本目录的测试运行在 openEuler RISC-V 环境下
- bpftrace V-state 数据由 `vstate_trace_cpu.bt` 采集，通过 `sched:sched_switch` tracepoint 间接判定 V 状态
- save 计数基于 `pt_regs->status.VS == DIRTY (0x0600)`
