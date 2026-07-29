# riscv-llm-scheduler

面向 RISC-V Vector 扩展的大语言模型推理并发调度优化（OS 高校赛题）

## 项目概述

LLM 推理是典型的高强度持续向量计算场景。在 RISC-V 架构中，每个 hart 拥有 32 个向量寄存器（v0–v31），单次完整 V 状态保存需要 512 字节以上的内存写入。Linux 默认调度策略（CFS）对向量密集型推理线程与普通线程一视同仁，导致推理线程在计算关键路径上被频繁抢占，积累的 V 状态切换开销造成整体吞吐下降与尾延迟恶化。

本项目的目标是：量化 V 状态上下文切换开销，并实现至少一种 V 状态感知的调度优化策略。

## 目录结构

```
riscv-llm-scheduler/
├── docs/                      # 技术调研与文档
│   ├── competition/           # 赛题说明与要求
│   ├── scheduler/             # 调度器调研（CFS、其他策略、性能损失分析）
│   ├── tracker/               # 跟踪工具与性能指标（perf、kprobe、affinity）
│   ├── setup/                 # 环境搭建指南（QEMU、板卡选型、llama.cpp）
│   ├── benchmark/             # 性能评测方法与指标定义
│   ├── x86-vstate/            # x86 V-state 调研
│   ├── llama-server-cr-fix/   # llama-server -Cr/-Crb 修复方案
│   └── tracker/量化相关脚本/   # 量化相关脚本
├── src/
│   ├── tracker/               # 量化分析工具（bpftrace 脚本，已实现）
│   ├── scheduler/             # 调度优化实现（vec_affine_sched，已实现）
│   └── benchmark/             # 性能评测脚本（基准测试 + 自动化测试运行器）
├── results/                   # 测试结果与数据
├── hardware/                  # 硬件配置与说明
└── .gitignore
```

## 源码模块说明

### 模型下载

本项目使用 Llama-3.2 Instruct Q4_K_M GGUF 量化模型：

| 模型 | 大小 | 下载链接 |
|------|:---:|------|
| Llama-3.2-1B-Instruct Q4_K_M | ~0.8 GB | [hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF](https://huggingface.co/hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF) |
| Llama-3.2-3B-Instruct Q4_K_M | ~2.1 GB | [hugging-quants/Llama-3.2-3B-Instruct-Q4_K_M-GGUF](https://huggingface.co/hugging-quants/Llama-3.2-3B-Instruct-Q4_K_M-GGUF) |

```bash
# 下载示例
huggingface-cli download hugging-quants/Llama-3.2-3B-Instruct-Q4_K_M-GGUF \
  llama-3.2-3b-instruct-q4_k_m.gguf --local-dir /path/to/models
```

### src/tracker/ — 量化分析工具（已实现）

通过 bpftrace 挂载 [sched:sched_switch](https://docs.kernel.org/trace/events.html) tracepoint，在内核上下文切换时从内核栈偏移读取 `pt_regs.status` 的 VS 域（`0x0600`），判断被切换进程的 V 状态是否 dirty，从而量化统计 V 状态 save/restore 次数。

| 文件 | 说明 |
|------|------|
| [vstate_trace.bt](src/tracker/vstate_trace.bt) | **基础版** — 全局统计 V 状态 save/restore 总次数，按进程名和 PID 分组。通过缓存 `task_struct` 指针 + `start_time` 校验解决 PID 复用问题，并区分 save 侧（当前被抢占进程）和 restore 侧（被调度回来的进程）。 |
| [vstate_trace_cpu.bt](src/tracker/vstate_trace_cpu.bt) | **per-CPU 增强版** — 在基础版之上增加了 per-CPU 统计维度：`@cpu_saves`、`@cpu_restores`、`@cpu_saves_by_proc`、`@tid_cpu_saves`、`@tid_cpu_restores`，可精确到每个 CPU 核上哪个线程发生了 V 状态切换，用于识别绑核后 V 状态泄漏。 |

**依赖**：RISC-V Linux 内核需开启 `CONFIG_BPF`、`CONFIG_BPF_EVENTS`，并安装 `bpftrace`。

---

### src/scheduler/ — 调度优化实现（已实现）

已实现多种 V 状态感知的调度优化策略，详细文档见 [src/scheduler/README.md](src/scheduler/README.md)。

| 模块 | 优先级 | 方案 |
|------|:---:|------|
| CPU affinity | 必做 | `taskset -c` 将推理线程绑定到指定物理核心 |
| cgroup cpuset | 必做 | 创建 cgroup 并设置 `cpuset.cpus` + `cpuset.cpus.partition` 实现双向隔离 |
| sched_ext | ~~可选~~ **已实现** | `vec_affine_sched` — BPF 可编程调度器，通过 Uprobe 精准识别推理线程并执行异构核心硬隔离 |
| sched_setattr | 可选 | 扩展调度提示区分 V 状态敏感线程 |

---

### src/benchmark/ — 性能评测脚本（已实现）

包含一套完整的自动化测试工具链，用于评估不同绑核/调度策略下的推理性能与 V 状态切换开销。

| 文件 | 说明 |
|------|------|
| [benchmark_llama.py](src/benchmark/benchmark_llama.py) | **基准测试主脚本**，支持 `qemu`、`local`、`remote` 普通模式，以及自动化远程实验矩阵（1B/3B/8B p4、3B p2/p4/p8/p16）。通过 bpftrace 采集轮次级 V-state，输出 Client TTFT、Server TTFT、Queue+Net、E2E、TPS 与汇总/逐请求 CSV。详见[性能评测指南](docs/benchmark/性能评测指南.md)。 |
| [bench_runner.py](src/benchmark/bench_runner.py) | **自动化测试运行器（单配置版）**，通过 SSH 连接板卡，自动启停 `llama-server` + `bpftrace`，发送 50 个并发 HTTP 推理请求，采集 TPS/TTFT/P95 和 V-state 计数，结果保存为 CSV。支持多种绑核/调度方法轮换。 |
| [bench_runner_all.py](src/benchmark/bench_runner_all.py) | **自动化测试运行器（多配置版）**，在 `bench_runner.py` 基础上预定义了 10 组测试配置（覆盖 Llama-3.2-1B/3B Q4_K_M 模型、不同线程数、上下文长度、并行度、prompt 长度、输出 token 数），自动遍历并输出结果。 |
| [quantification.py](src/benchmark/quantification.py) | **量化统计脚本**，用于 V 状态量化分析。 |

#### 支持的绑核/调度方法

| 方法 | 原理 | 隔离方向 |
|------|------|:--------:|
| `taskset` | `taskset -c X-Y` 绑定推理线程到指定核心 | 单向 |
| `cpuset` | cgroup 设置 `cpuset.cpus` + PID 移入 | 单向 |
| `partition-root` | cgroup + `echo root > cpuset.cpus.partition` | **双向** |
| `systemd-scope` | `AllowedCPUs` + `systemd-run --scope` | 单向（等效） |
| `taskset-chrt-b` | `taskset` + `chrt -b 0`（SCHED_BATCH） | 单向 + BATCH |
| `taskset-chrt-f` | `taskset` + `chrt -f 90`（SCHED_FIFO） | 单向 + FIFO |

#### 输出指标

| 指标 | 含义 | 说明 |
|------|:--:|------|
| TPS / agg_tps | token/s | 聚合吞吐量，越高越好 |
| Avg TTFT | ms | 平均首 token 延迟，越低越好 |
| P95 TTFT | ms | 95 分位首 token 延迟，越低越好 |
| v_saves | 次数 | V 状态保存总数 |
| v_restores | 次数 | V 状态恢复总数 |
| target_saves / nontarget_saves | 次数 | 目标/非目标核上的 V 状态 save 次数，用于检测绑核泄漏 |
| per-CPU saves | 次数 | 每个 CPU 核上的 V 状态 save 分布 |

#### 快速使用

```bash
# 宿主机安装依赖
pip install paramiko requests numpy

# 修改 bench_runner.py 中的 SSH_HOST / SSH_USER / SSH_PASS
# 以及板卡上的模型路径、bpftrace 脚本路径等配置

# 运行单组测试
python src/benchmark/bench_runner.py

# 运行多组对比测试
python src/benchmark/bench_runner_all.py
```

## 数据流

```
bench_runner.py (宿主机)
    │
    ├─ SSH → 板卡: 启动 llama-server（带绑核/调度参数）
    ├─ SSH → 板卡: 启动 bpftrace（vstate_trace_cpu.bt）
    ├─ SSH → 板卡: 部署并运行 HTTP 并发请求脚本
    ├─ SSH → 板卡: 轮询等待请求完成
    ├─ SSH → 板卡: 停止 bpftrace + 拉取 V 状态数据
    └─ 本地: 解析 JSON → 计算 TPS/TTFT/P95 → 输出 CSV
```

## 赛题要求与完成进度

| 模块 | 分值 | 说明 | 状态 |
|------|:----:|------|:----:|
| 量化分析工具 | 10 分 | eBPF/bpftrace 插桩统计 V 状态切换开销 | ✅ 已实现 |
| 调度优化实现 | 15 分 | CPU affinity / cgroup cpuset / sched_ext | ✅ 已实现 |
| 性能评测 | 10 分 | TPS、平均 TTFT、P95 TTFT | ✅ 已实现 |
| 技术报告 | 40 分 | 设计方案 + 实现方案 + 实验结果 + 特色创新 | ✅ 已实现 |
| 开发过程 | 20 分 | Git 规范 + README + 演示视频 | ⏳ 进行中 |

## License

本项目仅供学习研究使用。
