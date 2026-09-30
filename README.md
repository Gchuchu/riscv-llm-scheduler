# 🚀 面向 RISC-V Vector 扩展的大语言模型推理并发调度优化

<p align="center">
  <img src="https://img.shields.io/badge/platform-RISC--V%20(Milk--V%20Jupiter)-blue.svg" alt="Platform">
  <img src="https://img.shields.io/badge/vector-RVV%201.0%20VLEN%3D256-green.svg" alt="RVV">
  <img src="https://img.shields.io/badge/language-C%20%7C%20Python%20%7C%20bpftrace-orange.svg" alt="Language">
  <img src="https://img.shields.io/badge/scheduler-sched__ext-purple.svg" alt="Scheduler">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey.svg" alt="License">
</p>

<p align="center">
  <strong>🔥 基于 RISC-V Vector 扩展的 LLM 推理 V 状态感知调度优化</strong>
</p>

本项目是为"第三届中国研究生操作系统开源创新大赛"设计的参赛作品，在**国产 RISC-V 硬件平台（Milk-V Jupiter / SpacemiT K1）**上，通过 **eBPF/bpftrace 量化分析 + sched_ext 自定义调度器**，优化 LLM 推理场景下 RISC-V Vector 寄存器状态切换开销导致的吞吐下降与尾延迟恶化问题。

---

## 📚 项目概述

LLM 推理是典型的高强度持续向量计算场景。在 RISC-V 架构中，每个 hart 拥有 32 个向量寄存器（v0–v31），单次完整 V 状态保存需要 512 字节以上的内存写入。Linux 默认调度策略（CFS）对向量密集型推理线程与普通线程一视同仁，导致推理线程在计算关键路径上被频繁抢占，不断累积的 V 状态切换开销造成整体吞吐下降与尾延迟恶化。

本项目的核心目标是：**量化 V 状态上下文切换开销，优化推理的 TTFT 和 TPS**。

### 应用场景

在 Milk-V Jupiter（SpacemiT K1，RVV 1.0，VLEN=256）上用 llama.cpp 的 `llama-server` 做多请求并发推理，同时板上还有系统任务或其他 CPU 负载。推理线程被抢占时要保存、恢复向量寄存器。本仓库用来统计这类 V 状态切换，并把它和同一次运行的吞吐、TTFT 放在一起比较；再用绑核、cgroup 或 sched_ext 把矩阵乘 worker 与普通任务分开。

### ⭐ 核心亮点

- ✅ **完整的量化分析工具链**：基于 bpftrace 的 V 状态切换追踪，支持 per-CPU 细粒度统计
- ✅ **多层级调度优化方案**：CPU affinity / cgroup cpuset / sched_ext 自定义调度器全覆盖
- ✅ **自动化性能评测框架**：一键完成多模型、多并发、多策略的对比测试
- ✅ **异构核心硬隔离**：sched_ext 调度器实现推理线程与普通任务的物理核心隔离
- ✅ **国产化 RISC-V 方案**：基于 SpacemiT K1（RVV 1.0, VLEN=256）真实硬件验证

---

## 🏗️ 项目结构

```
riscv-llm-scheduler/
├── README.md
├── src/                        # 🎯 核心源代码
│   ├── tracker/                # 📊 量化分析工具（bpftrace 脚本）
│   │   ├── vstate_trace.bt           # 基础版：全局 V 状态统计
│   │   ├── vstate_trace_cpu.bt       # per-CPU 增强版：核级别 V 状态分布
│   │   ├── vstate_trace_llama_server.bt  # llama-server 专用：save/restore 与切换耗时
│   │   └── README.md
│   ├── scheduler/              # ⚙️ 调度优化实现
│   │   ├── vec_affine_sched.bpf.c    # sched_ext BPF 内核态调度引擎
│   │   ├── vec_affine_sched.c        # sched_ext 用户态 Loader
│   │   ├── 自定义调度.md              # sched_ext 设计文档
│   │   └── README.md
│   └── benchmark/              # 🧪 性能评测脚本
│       ├── benchmark_llama.py         # 基准测试主脚本（QEMU/本机/远程）
│       ├── bench_runner.py            # 自动化测试运行器（单配置）
│       ├── bench_runner_all.py        # 自动化测试运行器（多配置批量）
│       ├── bench_runner_final.py      # 终版场景运行器
│       ├── cb_ab.py                   # Continuous Batching 开关对照
│       ├── plot_cb_ab.py              # CB 对照结果绘图
│       ├── quantification.py          # V 状态量化统计脚本
│       ├── README.md
│       └── README_bench_runner.md
├── docs/                       # 📖 提交文档与技术调研
│   ├── 项目说明书.pdf
│   ├── 项目创新说明.md
│   ├── competition/            # 赛题说明与任务拆解
│   ├── scheduler/              # 调度器调研（CFS、其他策略、性能分析）
│   ├── tracker/                # 跟踪工具与性能指标调研
│   ├── setup/                  # 环境搭建指南（QEMU、板卡选型、llama.cpp）
│   ├── benchmark/              # 性能评测方法与指标定义
│   ├── x86-vstate/             # x86 "V"状态 横向对比调研
│   └── llama-server-cr-fix/    # llama-server -Cr/-Crb 修复方案
├── tests/                      # 📈 测试及验证材料
│   ├── rvv/                    # 真实 RVV 板卡 benchmark 数据
│   ├── quantification/         # V 状态量化实验结果
│   ├── sched_ext/              # 自定义调度器实验结果
│   ├── test/                   # 绑核/调度策略对比测试（group1~10）
│   ├── final_test/             # 终版场景与 stress-ng 分级负载
│   ├── benchmark/              # CB 开关对照原始结果与图
│   └── README.md
├── demo/                       # 🎥 实机运行录像
│   ├── 绑核+策略方法/
│   └── 自定义调度/
├── presentation/               # 🎤 决赛演示材料
│   ├── 决赛现场演示PPT.pptx
│   └── 决赛演示视频.mp4
├── hardware/                   # 🔌 硬件配置与使用说明
│   └── README.md
├── 作品原创承诺书.pdf
└── .github/                    # 🔄 CI 工作流与 PR 模板
```

---

## 📋 赛题要求与完成进度

### 项目代码（40 分）

| 子项 | 分值 | 评分标准 | 状态 |
|------|:----:|------|:----:|
| a) 代码功能实现度 | 15 分 | 实现 V 状态上下文切换开销的量化分析工具，统计 V 状态保存/恢复次数、riscv_vstate_save 调用频次、dirty V 线程被抢占事件，并量化其开销 | ✅ 已完成 |
| a) 代码功能实现度 | 10 分 | 实现至少一种 V 状态感知调度优化策略，功能完整、可正常运行 | ✅ 已完成 |
| b) 代码实现正确性与性能 | 10 分 | 在不少于 2 种并发场景下，TPS / 平均 TTFT / P95 TTFT 相比统一基线有稳定、可重复的改善 | ✅ 已完成 |
| c) 代码质量与风格 | 5 分 | 代码结构清晰，关键逻辑有注释，提供完整的构建与运行说明 | ✅ 已完成 |

### 技术报告（40 分）

| 子项 | 分值 | 评分标准 | 状态 |
|------|:----:|------|:----:|
| a) 设计方案 | 10 分 | V 状态切换问题分析准确，调度优化方案设计清晰，说明与现有 Linux 调度机制（CFS、lazy save）的关系 | ✅ 已完成 |
| b) 实现方案 | 10 分 | 实现细节描述完整，包含系统架构图或关键流程图，对已有相关工作（sched_ext、RVV 推理优化等）有准确调研与引用 | ✅ 已完成 |
| c) 运行效果/测试结果 | 10 分 | 测试数据规范（含测试环境描述、统一基线配置说明、均值与标准差、各并发档位结果），实验可复现，提供演示视频（3–5 分钟）；性能数据来自真实 RVV 1.0 硬件 | ✅ 已完成 |
| d) 特色创新 | 10 分 | 方案提出新的调度原语、用户态-内核协同机制，或对问题有更深层次的分析与拓展 | ✅ 已完成 |

### 开发过程（20 分）

项目管理使用飞书+github。

| 子项 | 分值 | 评分标准 | 状态 |
|------|:----:|------|:----:|
| e) 代码提交情况 | 15 分 | Git 提交历史规范，工作量分布合理，提交信息清晰，体现迭代过程 | ✅ 已完成 |
| f) 团队协作情况 | 5 分 | 成员分工明确，协作有效 | ✅  已完成 |

---

## ⭐ 主要功能

### 📊 量化分析工具

通过 bpftrace 挂载 `sched:sched_switch` tracepoint，在内核上下文切换时从内核栈指定偏移读取 `pt_regs.status` 的 VS 域，判断被切换进程的 V 状态是否 dirty，从而量化统计 V 状态 save/restore 次数。

| 脚本 | 功能 |
|------|------|
| `vstate_trace.bt` | **基础版** — 全局统计 V 状态 save/restore 总次数，按进程名和 PID 分组 |
| `vstate_trace_cpu.bt` | **per-CPU 增强版** — 每个 CPU 核上的 V 状态分布，用于识别绑核后 V 状态泄漏 |
| `vstate_trace_llama_server.bt` | **llama-server 专用版** — 只统计 `llama-server` 的切换，并区分 V 状态与非 V 状态切换耗时 |

> 💡 不依赖 `riscv_vstate_save` 的具体内核符号，通过 tracepoint 接口采集，兼容性更好。

### ⚙️ 调度优化实现

已实现多种 V 状态感知的调度优化策略：

| 方案 | 原理 | 隔离方向 |
|------|------|:--------:|
| CPU affinity | `taskset -c X-Y` 将推理线程绑定到指定核心 | 单向 |
| cgroup cpuset | cgroup 设置 `cpuset.cpus` + PID 移入 | 单向 |
| partition-root | cgroup + `cpuset.cpus.partition=root`，独占核心 | **双向** |
| systemd-scope | `AllowedCPUs` + `systemd-run --scope` | 单向 |
| taskset + SCHED_BATCH | `taskset` + `chrt -b 0` | 单向 + BATCH |
| taskset + SCHED_FIFO | `taskset` + `chrt -f 90` | 单向 + FIFO |
| **sched_ext** | `vec_affine_sched` — BPF 可编程调度器，异构核心硬隔离 | **双向** |

#### 🧠 vec_affine_sched — sched_ext 自定义调度器

基于 Linux sched_ext 框架实现的 RISC-V 异构核心感知调度器。通过 eBPF + Uprobe 实现 LLM 推理线程的精准识别与硬隔离：

- **Uprobe 精准识别**：挂载 `ggml_compute_forward_mul_mat` 函数，标记真正的矩阵乘法推理线程
- **异构核心硬隔离**：推理 worker 绑定 AI 计算核心（CPU 0至3），普通任务隔离到通用核心（CPU 4至7）
- **Prev-CPU 热 Cache 归巢**：唤醒推理线程时优先检测原核是否空闲，保留 L1/L2 Cache 热度
- **差异化时间片**：推理线程 30ms 长时间片 vs 普通任务 2ms 短时间片

### 🧪 性能评测框架

完整的自动化测试工具链，支持多模型、多并发、多策略的对比测试：

| 工具 | 功能 |
|------|------|
| `benchmark_llama.py` | 基准测试主脚本，支持 QEMU/本机/远程板卡，含自动化实验矩阵 |
| `bench_runner.py` | 自动化测试运行器（单配置），SSH 连接板卡，自动启停 server + bpftrace |
| `bench_runner_all.py` | 多配置批量测试，预定义 10 组测试配置自动遍历 |
| `bench_runner_final.py` | 终版场景运行器：构建版本、`-Cr`、sched_ext、stress-ng |
| `cb_ab.py` | Continuous Batching 开关对照（单波 / 多请求闭环） |
| `quantification.py` | V 状态量化统计脚本 |

**📊 输出指标**：

| 指标 | 单位 | 说明 |
|------|:--:|------|
| TPS / agg_tps | token/s | 聚合吞吐量，越高越好 |
| Avg TTFT | ms | 平均首 token 延迟，越低越好 |
| P95 TTFT | ms | 95 分位首 token 延迟，越低越好 |
| v_saves | 次 | V 状态保存总数 |
| v_restores | 次 | V 状态恢复总数 |
| target_saves / nontarget_saves | 次 | 目标/非目标核上的 V 状态 save，用于检测绑核泄漏 |

---

## 🛠️ 系统环境

### 🔌 硬件要求

| 项目 | 说明 |
|------|------|
| 型号 | Milk-V Jupiter（K1） |
| 主控 | SpacemiT K1（1.6GHz, 8 核） |
| RAM | 8GB |
| RVV | 1.0, VLEN=256 |

### 💻 软件环境

| 项目 | 说明 |
|------|------|
| 操作系统 | Ubuntu / openEuler（RISC-V） |
| 内核 | Linux 6.12+（需开启 `CONFIG_SCHED_CLASS_EXT`、`CONFIG_BPF`） |
| 编译器 | SPACEMIT RISC-V GCC 交叉编译工具链 |
| bpftrace | 用于 V 状态追踪 |
| Python 3 | 用于 benchmark 脚本（宿主机） |
| llama.cpp | 支持 RVV 1.0 的 llama-server |

实验板卡为 openEuler，内核 6.18.38。开启 sched_ext 的移植步骤见 [OS移植方法](docs/setup/OS移植方法.md)。

### 🔬 QEMU 验证环境

```bash
qemu-system-riscv64 -cpu rv64,v=true,vlen=256,elen=64,vext_spec=v1.0
```

---

## 依赖要求

| 位置 | 依赖 |
|------|------|
| 板卡内核 | `CONFIG_BPF`、`CONFIG_BPF_EVENTS`、`CONFIG_DEBUG_INFO_BTF`；使用 sched_ext 时再开启 `CONFIG_SCHED_CLASS_EXT` |
| 板卡软件 | `bpftrace`、`clang`、`bpftool`、`gcc`、libbpf、libelf、zlib；`python3`、`python3-requests`、`curl`；绑核批量测试另需 `python3-numpy`；分级负载测试另需 `stress-ng` |
| 宿主机 | Python 3；`pip install paramiko requests numpy`。重绘 CB 对照图另需 `matplotlib` |
| 模型与服务 | 支持 RVV 1.0 的 `llama-server`，以及 Llama-3.2 Instruct Q4_K_M GGUF |

bpftrace 脚本和 Python 评测脚本没有单独的安装包。`llama-server` 不在本仓库内编译。

---

## 构建方法

追踪脚本与评测脚本无需编译。需要在板卡上编译的是 `src/scheduler/` 中的 `vec_affine_sched`。在该目录执行：

```bash
# 1. 从运行中的内核导出类型
bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h

# 2. 编译 BPF 字节码（include 换成当前内核树）
clang -g -O2 -target bpf -D__TARGET_ARCH_riscv \
    -I/path/to/linux/tools/sched_ext/include \
    -I. -c vec_affine_sched.bpf.c -o vec_affine_sched.bpf.o

# 3. 生成 skeleton
bpftool gen skeleton vec_affine_sched.bpf.o > vec_affine_sched.skel.h

# 4. 编译 loader（libbpf 与头文件路径换成当前内核树）
gcc -g -O2 -Wall -I. \
    -I/path/to/linux/tools/include \
    -I/path/to/linux/tools/lib \
    vec_affine_sched.c /path/to/linux/tools/lib/bpf/libbpf.a \
    -lelf -lz -o vec_affine_sched
```

实验机上的完整 include 路径见 [src/scheduler/README.md](src/scheduler/README.md)。内核配置与交叉编译见 [OS移植方法](docs/setup/OS移植方法.md)。

终版实验使用两套已装到板卡上的 llama.cpp：优化版（spacemit）和普通版（通用 RVV）。二进制与 `libggml-cpu.so` 路径写在 `src/benchmark/bench_runner_final.py` 的 `LLAMA_BUILDS` 中，换环境时改这里。

---

## 📦 模型下载

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

---

## 🚀 运行方法

追踪和 `llama-server`、sched_ext 在板卡上运行。评测脚本在宿主机运行，通过 SSH 操作板卡。调度器需先按上一节编译。

### ⚡ 1. 性能 Governor 设置

测试前固定到 `performance` 模式：

```bash
sudo cpupower frequency-set -g performance
```

测试完成后恢复：

```bash
sudo cpupower frequency-set -g conservative
```

### 📡 2. V 状态追踪

```bash
# 基础版 — 全局统计
sudo bpftrace src/tracker/vstate_trace.bt

# per-CPU 增强版 — 推荐
sudo bpftrace src/tracker/vstate_trace_cpu.bt

# llama-server 专用版 — 只统计该进程，并记录切换耗时
sudo bpftrace src/tracker/vstate_trace_llama_server.bt

# 输出重定向到文件
sudo bpftrace src/tracker/vstate_trace_cpu.bt > /tmp/vstate_result.log
```

### 🦙 3. 推理服务启动

```bash
# 基础启动
./llama-server-rvv -m Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --port 8080 -c 8192 --host 0.0.0.0 --parallel 8

# 带绑核启动（优化方案）
taskset -c 4-7 ./llama-server-rvv -m Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --port 8080 -c 8192 --host 0.0.0.0 --parallel 4 -t 4
```

### 🧠 4. sched_ext 调度器部署

```bash
# 基本运行（无 Uprobe 精准标记）
./vec_affine_sched

# 挂载 Uprobe 到 libggml-cpu.so，精准识别推理线程
./vec_affine_sched -t /path/to/libggml-cpu.so
```

### 🧪 5. Benchmark 测试

```bash
# 宿主机安装依赖
pip install paramiko requests numpy

# 本机模式
python src/benchmark/benchmark_llama.py --mode local --concurrency 2,4,8 --trials 3

# 远程板卡（普通模式）
python src/benchmark/benchmark_llama.py \
  --mode remote --host <板卡IP> --ssh-user root \
  --concurrency 2,4,8 --trials 3

# 自动化实验矩阵（远程板卡）
python src/benchmark/benchmark_llama.py \
  --mode remote --host <板卡IP> --ssh-user root \
  --experiment-matrix full \
  --server-binary /root/llama-server-rvv \
  --server-threads 4 --context-per-slot 1024 \
  --output-dir results

# 一键多组对比测试
python src/benchmark/bench_runner_all.py

# 终版场景（先改 SCENARIOS / RUN 和板卡路径）
python src/benchmark/bench_runner_final.py
```

运行前把 `bench_runner.py`、`bench_runner_all.py`、`bench_runner_final.py` 顶部的板卡地址、SSH、模型路径和结果目录改成当前环境。

---

## 🔄 数据流

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

---

## 💡 技术特色

### 🏆 创新点

1. **RISC-V V 状态量化方法**：通过 bpftrace 挂载 `sched_switch` tracepoint 读取 `pt_regs.status` 的 VS 域，通用性优于 kprobe 挂载特定内核符号
2. **异构核心硬隔离调度器**：基于 sched_ext 框架实现推理线程与普通任务的物理核心隔离，结合 Uprobe 实现线程级精准识别
3. **自研自动化评测框架**：一套脚本覆盖 QEMU 验证、本机测试、远程板卡实验矩阵，自动管理 server 生命周期与V状态信息同步采集
4. **多方案系统对比**：7 种绑核/调度策略的统一对比框架，定量评估各方案效果

### 🎯 技术优势

- **完整工具链**：从量化分析到调度优化到性能评测的全链路覆盖
- **硬件无关设计**：bpftrace 追踪方案基于 tracepoint 通用接口，兼容不同 RISC-V 内核
- **自动化程度高**：bench_runner 一键完成 server 启停、bpftrace 采集、请求发送、结果汇总
- **可复现性强**：所有实验参数、server 命令、模型等均记录在 CSV 中

---

## 测试方法

| 目的 | 命令 | 归档结果 |
|------|------|----------|
| 绑核 / 调度策略，10 组参数 | `python src/benchmark/bench_runner_all.py` | [tests/test/](tests/test/README.md) |
| 单组参数轮换方法 | `python src/benchmark/bench_runner.py` | 脚本配置的结果目录 |
| 构建版本、`-Cr`、sched_ext、stress-ng | `python src/benchmark/bench_runner_final.py` | [tests/final_test/](tests/final_test/README.md) |
| 模型规模与并发矩阵 | `benchmark_llama.py --experiment-matrix full` | [tests/rvv/](tests/rvv/README.md)，索引见 [tests/README.md](tests/README.md) |
| Continuous Batching 开关 | `python src/benchmark/cb_ab.py` | [tests/benchmark/cb-ab/](tests/benchmark/cb-ab/)，说明见 [CB开关对照实验](docs/benchmark/CB开关对照实验.md) |
| sched_ext 与 CFS | 先运行 `vec_affine_sched`，再用评测脚本 | [tests/sched_ext/](tests/sched_ext/README.md) |

`bench_runner*.py` 的 CSV 写到脚本里的结果目录（实验时为宿主机上的 `D:/比赛/results/`）。仓库里的 `tests/` 是归档副本。

CB 对照在宿主机设置 `BOARD_HOST`、`BOARD_USER`、`BOARD_PASS`、`LLAMA_SERVER_BIN`、`LLAMA_MODEL_PATH` 后执行。绘图：

```bash
python src/benchmark/cb_ab.py --concurrency 8 --requests 24 --trials 3 --warmup 1 --tag cb8
python src/benchmark/plot_cb_ab.py
```

指标口径、控制变量和字段见 [性能评测指南](docs/benchmark/性能评测指南.md)。单配置运行器的方法顺序见 [README_bench_runner.md](src/benchmark/README_bench_runner.md)。

---

## 🧪 测试验证

### 关键实验观察

六种通用方法的正式评测基于统一的 Linux 环境。每种方法在 6 组参数配置下独立执行 3 次，共形成 108 轮试验和 5400 条请求记录；冷启动样本在稳态分析中单独处理。

| 对比项 | 基准 (taskset) | 优化方法 | 观测结果 |
|--------|:-------------:|----------|----------|
| partition-root TPS | 0.90 | 0.90 | 吞吐接近 |
| partition-root V/ktok | 13111 | 5298 | 降低 **59.6%** |
| taskset-chrt-f V/ktok | 13111 | 1164 | 降低 **91.1%** 、但 TPS 由 0.90 降至 0.86 |

结果表明：调度策略能够明显改变 V 状态条件事件的发生频度，但事件减少并不必然同步转化为吞吐率或首 token 延迟改善。

### 🧠 sched_ext 自定义调度器验证

`sched_ext` 原型在独立的新内核环境中完成验证。该组结果与六种通用方法的内核和参数配置不同，只进行组内比较，不参与正式六方案排名。

| 指标 | CFS  | 自定义调度器 | 变化 |
|------|:------------------:|:------------:|------|
| TPS | 0.50 | 1.02 | **+104%** |
| Avg TTFT (ms) | 2260 | 1730 | **-23.5%** |
| P95 TTFT (ms) | 3439 | 4944 | +43.8% |

在特定高负载配置下，启用自定义调度器后吞吐翻倍、平均首 token 延迟显著下降；但 P95 TTFT 有所上升。

#### sched_ext 核心优势

- **吞吐翻倍**：TPS 从 0.50 提升至 1.02，推理吞吐实现 104% 增长
- **延迟优化**：平均 TTFT 降低 23.5%，用户体验显著改善
- **物理隔离**：异构核心硬隔离避免推理线程与系统任务争抢 CPU 资源
- **精准识别**：Uprobe 级推理线程标记，比传统 PID 匹配更准确
- **可编程调度**：用户态 BPF 实现调度逻辑，无需修改内核源码即可灵活调整策略

### 稳定性与规模测试

- 多模型规模测试：1B / 3B 
- 多并发测试：p2 / p4 / p8 / p16
- 每组配置多次重复试验

### ⚡ 测试环境

- 真实 RISC-V 硬件（Milk-V Jupiter, SpacemiT K1, RVV 1.0, VLEN=256）
- TPS、TTFT、P95 延迟与 V 状态切换频次的多维度对比

---

## 🎥 演示视频

演示视频（约 5 分钟）展示了 V 状态量化分析、调度优化效果与性能评测的完整流程。

> 📥 **下载链接**：[百度网盘 - 第三届参赛视频](https://pan.baidu.com/s/1KA0q4gDr-5d6B1QJSDIqvQ?pwd=qxji)
> 🔑 提取码：qxji

---

## 已知限制

- `vec_affine_sched` 按 K1 的核分组编写：矩阵乘 worker 放在 CPU 0–3，普通任务放在 CPU 4–7。
- V 状态 save/restore 是次数。一次计数覆盖整轮请求，不能分到单条 HTTP 请求，次数本身也不能换成微秒。切换耗时由 `vstate_trace_llama_server.bt` 在 `finish_task_switch` 上另行统计。更换内核、板卡或厂商补丁后，需要重新核对 BTF、`task_struct` 和内核栈布局。说明见 [性能评测指南](docs/benchmark/性能评测指南.md)。
- 推理线程已经占住目标核时，保存次数下降不一定带来吞吐上升。见「测试验证」。
- `tests/sched_ext/` 与六种绑核方法的内核和参数不同，只在组内比较。
- `bench_runner.py`、`bench_runner_all.py`、`bench_runner_final.py` 把 SSH、板上路径和结果目录写在脚本里。`src/scheduler/README.md` 里的编译 include 路径对应当时的实验机内核树。换环境需要自行修改。
- 分级负载使用 `stress-ng --cpu-method all`。同一矩阵内参数一致，但单次负载稳定性低于固定压力方法。

---

## 📚 参考资料

- [RISC-V Vector Extension Specification v1.0](https://github.com/riscv/riscv-v-spec)
- [Linux Kernel: Vector Extension Support for RISC-V](https://docs.kernel.org/arch/riscv/vector.html)
- [Linux sched_ext Documentation (6.12+)](https://docs.kernel.org/scheduler/sched-ext.html)
- [llama.cpp RVV 1.0 k-quant kernels (PR #12530)](https://github.com/ggml-org/llama.cpp/pull/12530)
- [V-Seek: Efficient LLM Inference on 64-core RISC-V Platform](https://arxiv.org/abs/2503.17422)

---

## 📄 许可证

本项目基于 MIT License 开源协议。

---

<p align="center">
  <strong>🏆 第三届中国研究生操作系统开源创新大赛 参赛作品</strong>
</p>

<p align="center">
  <em>Built with ❤️ for RISC-V & Open Source Community</em>
</p>
