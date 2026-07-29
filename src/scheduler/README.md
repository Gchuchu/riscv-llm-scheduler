# riscv-llm-scheduler 调度优化

## 功能

实现 V 状态感知的调度优化策略，减少推理线程被抢占时 V 状态 save/restore 开销。

## 方案规划

### 保底方案（必做）

| 方案 | 实现方式 | 隔离方向 | bench_runner 集成 |
|------|----------|:--------:|:-----------------:|
| CPU affinity | `taskset -c X-Y` 将推理线程绑定到指定物理核心 | 单向 | ✅ 已集成 |
| cgroup cpuset | 创建 cgroup + 设置 `cpuset.cpus` + 移入 PID | 单向 | ✅ 已集成 |
| partition-root | cgroup + `echo root > cpuset.cpus.partition`，独占核心（其他进程无法使用） | **双向** | ✅ 已集成 |
| systemd-scope | `AllowedCPUs` + `systemd-run --scope`，限制系统进程使用目标核 | 单向（等效） | ✅ 已集成 |

上述四种方法已在 [bench_runner.py](../benchmark/bench_runner.py) 中实现，可作为自动化测试脚本直接使用。详细用法参见 [README_bench_runner.md](../benchmark/README_bench_runner.md)。

### 进阶方案（可选）

| 方案 | 说明 | 当前状态 |
|------|------|:--------:|
| sched_ext | BPF 可编程调度器（`vec_affine_sched`），在用户态编写调度逻辑，动态加载到内核 | ✅ 已实现 |
| taskset + 调度策略 | `chrt -b 0`（SCHED_BATCH）/ `chrt -f 90`（SCHED_FIFO）配合 taskset 使用 | ✅ 已集成 |

---

## vec_affine_sched — sched_ext 自定义调度器

基于 Linux sched_ext 框架实现的 RISC-V 异构核心感知调度器，通过 eBPF + Uprobe 实现线程级的 LLM 推理线程精准识别与异构核心硬隔离，专为大模型推理（llama.cpp）优化。

详细设计文档见 [自定义调度.md](自定义调度.md)。

### 核心特性

- **Uprobe 级线程精准识别**：通过挂载 `ggml_compute_forward_mul_mat` 函数的 uprobe/uretprobe，将真正执行矩阵乘法的线程标记为推理 worker，区分于 LLM 主线程与普通系统任务。
- **异构核心硬隔离**：推理 worker 线程强绑定 AI 计算核心（CPU 0~3），普通任务隔离到通用核心（CPU 4~7），实现物理算力的绝对隔离。
- **Prev-CPU 热 Cache 归巢**：唤醒推理线程时优先检测原核是否空闲，保留 L1/L2 Cache 与 Vector 寄存器热度。
- **差异化时间片与优先级**：推理线程赋予 20ms 长时间片，唤醒时直插 Local DSQ 队列头部；普通任务赋予 4ms 时间片，推入 Shared DSQ。
- **安全降级回退**：loader 退出时自动销毁 BPF struct_ops，平滑回退至原生 CFS 调度器。

### 文件说明

| 文件 | 说明 |
|------|------|
| [vec_affine_sched.bpf.c](vec_affine_sched.bpf.c) | **内核态 BPF 调度引擎** — 实现 `select_cpu`、`enqueue`、`dispatch`、`tick` 回调，通过 uprobe/uretprobe 精准识别推理线程，执行异构核心硬隔离策略 |
| [vec_affine_sched.c](vec_affine_sched.c) | **用户态 Loader** — 基于 libbpf 加载 BPF 字节码、挂载 struct_ops、附着 uprobe、Pin BPF Map、定时读取 Per-CPU 统计指标 |
| [自定义调度.md](自定义调度.md) | **设计文档** — 核心痛点分析、设计目标、架构分层、调度策略详解、验证测试方案 |

### 编译

环境要求：RISC-V Linux 内核需开启 `CONFIG_SCHED_CLASS_EXT`、`CONFIG_BPF`、`CONFIG_DEBUG_INFO_BTF` 等选项。

```bash
# 1. 生成 vmlinux.h（内核类型定义）
bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h

# 2. 编译 BPF 字节码
clang -g -O2 -target bpf -D__TARGET_ARCH_riscv \
    -I/home/gh/linux/linux-6.18.38/tools/sched_ext/include \
    -I. -c vec_affine_sched.bpf.c -o vec_affine_sched.bpf.o

# 3. 生成 BPF Skeleton 头文件
bpftool gen skeleton vec_affine_sched.bpf.o > vec_affine_sched.skel.h

# 4. 编译用户态 Loader
gcc -g -O2 -Wall \
    -I. \
    -I/home/gh/linux/linux-6.18.38/tools/include \
    -I/home/gh/linux/linux-6.18.38/tools/lib \
    vec_affine_sched.c \
    /home/gh/linux/linux-6.18.38/tools/lib/bpf/libbpf.a \
    -lelf -lz -o vec_affine_sched
```

### 运行

```bash
# 基本运行（无 Uprobe 精准标记，仅通过 PID 匹配推理进程）
./vec_affine_sched

# 挂载 Uprobe 到 libggml-cpu.so 的 ggml_compute_forward_mul_mat 函数，精准识别推理线程
./vec_affine_sched -t /home/gh/workspace/llama/debug/install_output/lib/libggml-cpu.so

# 自定义参数
./vec_affine_sched -t /path/to/libggml-cpu.so --pin-dir /sys/fs/bpf/vecsched --stats-interval 5
```

**运行参数**：

| 参数 | 说明 | 默认值 |
|------|------|:------:|
| `-t`, `--target-bin` | 目标 .so 文件路径（用于 uprobe 挂载 `ggml_compute_forward_mul_mat`） | 无（不使用 uprobe） |
| `-d`, `--pin-dir` | BPF Map Pin 目录 | `/sys/fs/bpf/vecsched` |
| `-i`, `--stats-interval` | 统计输出间隔（秒） | 5 |

**统计指标**（每 5 秒输出）：

| 指标 | 含义 |
|------|------|
| `enqueue_total` | 入队总次数 |
| `select_worker_ai_core` | 推理 worker 分配到 AI 核心（CPU 0~3）的次数 |
| `select_other_core` | 普通任务分配到通用核心（CPU 4~7）的次数 |
| `inference_threads` | 识别到的推理 worker 线程数 |
| `protect_window` | 推理线程在 mul_mat 计算期间免受抢占的保护窗口触发次数 |

### 关联工具

bench_runner 已支持以下调度方法组合的自动化测试：

```bash
python ../benchmark/bench_runner.py
# 在 METHODS 列表中配置需要测试的方法
```

生成的 CSV 结果中包含 `v_saves`、`nontarget_saves` 等 V 状态指标，用于量化评估各方案的优化效果。

### 当前状态

✅ sched_ext 自定义调度器 `vec_affine_sched` 已实现，bench_runner 已完成绑核/调度方法的自动化框架集成。
