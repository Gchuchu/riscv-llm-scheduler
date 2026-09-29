# riscv-llm-scheduler 性能评测

## 功能

包含一套完整的自动化测试工具链，用于评估不同绑核/调度策略下 RISC-V LLM 推理的性能与 V 状态切换开销。

## 测试脚本

| 文件 | 说明 |
|------|------|
| [benchmark_llama.py](benchmark_llama.py) | **基准测试主脚本**，支持 `qemu`、`local`、`remote` 普通模式和自动化远程实验矩阵。矩阵可运行 1B/3B/8B p4 与 3B p2/p4/p8/p16，自动管理 server 生命周期，输出轮次汇总和逐请求 CSV。 |
| [bench_runner.py](bench_runner.py) | **自动化测试运行器（单配置版）**，通过 SSH 连接板卡，自动启停 `llama-server` + `bpftrace`，发送 50 个并发 HTTP 推理请求，采集 TPS/TTFT/P95 和 V-state 计数，结果保存为 CSV。支持多种绑核/调度方法轮换。 |
| [bench_runner_all.py](bench_runner_all.py) | **自动化测试运行器（多配置版）**，在 `bench_runner.py` 基础上预定义了 10 组测试配置（覆盖 1B/3B 模型、不同线程数、上下文长度、并行度），自动遍历并输出结果。 |
| [bench_runner_final.py](bench_runner_final.py) | **自动化测试运行器（最终版）**，由 `SCENARIOS` 字典 + `RUN` 列表驱动的场景配置版：支持 sched_ext 场景级常驻、`cpu_stress` 压测、`stress-ng` 分级负载（20–100% 占空比，负载先于 server 启动）、`-Cr` 绑核，自动遍历 2/4/8 并发 × 3 trial；每轮结束立即落盘 CSV，中断最多丢当前一轮。 |
| [quantification.py](quantification.py) | **量化统计脚本**，用于 V 状态量化分析。 |

## 支持的绑核/调度方法

| 方法 | 原理 | 隔离方向 |
|------|------|:--------:|
| `taskset` | `taskset -c X-Y` 绑定推理线程到指定核心 | 单向 |
| `cpuset` | cgroup 设置 `cpuset.cpus` + PID 移入 | 单向 |
| `cpu-range` | `taskset` + `--cpu-strict 1` | 单向 |
| `partition-root` | cgroup + `echo root > cpuset.cpus.partition` | **双向** |
| `systemd-scope` | `AllowedCPUs` + `systemd-run --scope` | 单向（等效） |
| `taskset-chrt-b` | `taskset` + `chrt -b 0`（SCHED_BATCH） | 单向 + BATCH |
| `taskset-chrt-f` | `taskset` + `chrt -f 90`（SCHED_FIFO） | 单向 + FIFO |

## 输出指标

| 指标 | 含义 | 说明 |
|------|:--:|------|
| agg_tps | token/s | 聚合吞吐量，越高越好 |
| avg_ttft_ms | ms | 平均首 token 延迟，越低越好 |
| p95_ttft_ms | ms | 95 分位首 token 延迟，越低越好 |
| v_saves | 次数 | V 状态保存总数 |
| v_restores | 次数 | V 状态恢复总数 |
| target_saves | 次数 | 目标核上的 V 状态 save，正常应集中在目标核 |
| nontarget_saves | 次数 | 非目标核上的 V 状态 save，>0 表示绑核泄漏 |
| per-CPU saves | 次数 | 每个 CPU 核上的 V 状态 save 分布 |

## 测试场景

- 模型：[Llama-3.2-1B](https://huggingface.co/hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF) / [Llama-3.2-3B](https://huggingface.co/hugging-quants/Llama-3.2-3B-Instruct-Q4_K_M-GGUF) Instruct Q4_K_M
- 并发数：2 / 4 / 8
- Prompt 长度：64 / 128 / 256 token
- 输出 token 数：32 / 64 / 128 / 256
- 每个配置重复：3 次 trial

## 使用方法

### 方式一：benchmark_llama.py（灵活模式）

```bash
# QEMU 模式
python benchmark_llama.py --mode qemu --tag baseline

# 一键对比（baseline → optimized）
python benchmark_llama.py --mode local --compare

# 远程板卡
python benchmark_llama.py --mode remote --host 192.168.x.x

# 自动化远程矩阵：模型规模实验 + 3B 并发扫描
python benchmark_llama.py --mode remote --host <BOARD_IP> --ssh-user root \
  --experiment-matrix full --server-binary /root/llama-server-rvv \
  --server-threads 4 --server-context 4096 \
  --parallel-sweep-context-per-slot 1024 --output-dir results
```

自动化矩阵每轮通过客户端启动屏障并发发送 32 条请求，不做批内 warmup 排除；V-state、吞吐和延迟统计覆盖同一轮完整请求。完整参数、指标边界和结果字段见[性能评测指南](../../docs/benchmark/性能评测指南.md)。

### 方式二：bench_runner.py（单配置自动化）

```bash
# 安装依赖（宿主机）
pip install paramiko requests numpy

# 修改 bench_runner.py 中的 SSH_HOST/SSH_USER/SSH_PASS 等配置
# 然后直接运行
python bench_runner.py
```

### 方式三：bench_runner_all.py（多配置批量）

```bash
# 自动遍历 10 组测试配置
python bench_runner_all.py
```

### 方式四：bench_runner_final.py（最终版）

```bash
# 安装依赖（宿主机）
pip install paramiko

# 场景定义在 SCENARIOS 字典、执行顺序由 RUN 列表决定，改完直接运行
python bench_runner_final.py
```

场景字段：`build`（优化版/普通版）、`cpus`+`method`（`-Cr` 绑核）、`sched`（sched_ext 场景级常驻，trial 间不重启）、`stress`（`cpu_stress` 4 线程压 0-3）、`stress_ng`/`loads`（单档/多档 stress-ng 负载，多档在场景内逐档循环）、`par`（并发）、`trials`（重复次数）。切换实验只需改 `RUN` 列表，不用改代码或删配置。

## 数据流

```
宿主机                       板卡 (RISC-V)
─────                        ────────────
bench_runner.py
    │
    ├─ SSH ───────────────→  pkill llama-server
    ├─ SSH ───────────────→  pre_hook (创建 cgroup / systemd scope)
    ├─ SSH ───────────────→  nohup llama-server -m model ...
    ├─ SSH ───────────────→  nohup bpftrace vstate_trace_cpu.bt
    ├─ SSH ───────────────→  python3 b.py (50 个并发 HTTP 请求)
    │                           ├─ POST /v1/chat/completions
    │                           └─ 结果 → /tmp/bench_result.json
    ├─ 轮询等待 ──────────→  cat /tmp/bench_result.json
    ├─ SSH ───────────────→  pkill bpftrace
    ├─ SSH ───────────────→  cat /tmp/vstate_result.log
    │
    └─ 本地保存 CSV (TPS / TTFT / P95 / V-saves)
```

## 输出文件

每个方法跑完生成两个 CSV：

| 文件 | 内容 |
|------|------|
| `results/{group}_{method}.csv` | 汇总，每 trial 一行（TPS/TTFT/V-saves） |
| `results/{group}_{method}_detail.csv` | 明细，每请求一行（TTFT/TPS/E2E） |

## 详细文档

参见 [README_bench_runner.md](README_bench_runner.md) 获取 bench_runner 的完整使用说明。

## 依赖

- 宿主机：`pip install paramiko requests numpy`
- 板卡：`python3-requests python3-numpy curl bpftrace`

## 当前状态

✅ 已完成（benchmark_llama.py + bench_runner.py + bench_runner_all.py + bench_runner_final.py + quantification.py）
